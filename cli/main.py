from typing import Optional
import atexit
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import typer
import questionary
from pathlib import Path
from functools import wraps
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv(".env.enterprise", override=False)
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *
from cli.announcements import fetch_announcements, display_announcements
from cli.stats_handler import StatsCallbackHandler
from tradingagents.agents.utils.agent_utils import is_chinese_output_language

console = Console()

SOCIAL_BROWSER_SESSION_FILE = ".tradingagents_social_browser.json"


def localized_report_label(label: str) -> str:
    if not is_chinese_output_language():
        return label
    return {
        "Analyst Team Reports": "分析师团队报告",
        "Market Analyst": "市场分析师",
        "Social Analyst": "社媒情绪分析师",
        "News Analyst": "新闻分析师",
        "Fundamentals Analyst": "基本面分析师",
        "Market Analysis": "市场分析",
        "Social Sentiment": "社媒情绪",
        "News Analysis": "新闻分析",
        "Fundamentals Analysis": "基本面分析",
        "Research Team Decision": "研究团队决策",
        "Bull Researcher": "多方研究员",
        "Bear Researcher": "空方研究员",
        "Research Manager": "研究经理",
        "Bull Researcher Analysis": "多方研究员观点",
        "Bear Researcher Analysis": "空方研究员观点",
        "Research Manager Decision": "研究经理决策",
        "Trading Team Plan": "交易团队计划",
        "Trader": "交易员",
        "Risk Management Team Decision": "风险管理团队决策",
        "Aggressive Analyst": "激进风险分析师",
        "Conservative Analyst": "保守风险分析师",
        "Neutral Analyst": "中性风险分析师",
        "Aggressive Analyst Analysis": "激进风险分析师观点",
        "Conservative Analyst Analysis": "保守风险分析师观点",
        "Neutral Analyst Analysis": "中性风险分析师观点",
        "Portfolio Management Decision": "投资组合管理决策",
        "Portfolio Manager Decision": "投资组合经理决策",
        "Portfolio Manager": "投资组合经理",
    }.get(label, label)


def localized_risk_label(label: str) -> str:
    return localized_report_label(label)


app = typer.Typer(
    name="TradingAgents",
    help="BigA-Analysis-Agents CLI: China A-share Multi-Agent Analysis Framework",
    add_completion=True,  # Enable shell completion
)


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    # Fixed teams that always run (not user-selectable)
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Analyst name mapping
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    }

    # Report section mapping: section -> (analyst_key for filtering, finalizing_agent)
    # analyst_key: which analyst selection controls this section (None = always included)
    # finalizing_agent: which agent must be "completed" for this report to count as done
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Social Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._processed_message_ids = set()

    def init_for_analysis(self, selected_analysts):
        """Initialize agent status and report sections based on selected analysts.

        Args:
            selected_analysts: List of analyst type strings (e.g., ["market", "news"])
        """
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # Build agent_status dynamically
        self.agent_status = {}

        # Add selected analysts
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # Add fixed teams
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # Build report_sections dynamically
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        # Reset other state
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._processed_message_ids.clear()

    def get_completed_reports_count(self):
        """Count reports that are finalized (their finalizing agent is completed).

        A report is considered complete when:
        1. The report section has content (not None), AND
        2. The agent responsible for finalizing that report has status "completed"

        This prevents interim updates (like debate rounds) from counting as completed.
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # Report is complete if it has content AND its finalizing agent is done
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": localized_report_label("Market Analysis"),
                "sentiment_report": localized_report_label("Social Sentiment"),
                "news_report": localized_report_label("News Analysis"),
                "fundamentals_report": localized_report_label("Fundamentals Analysis"),
                "investment_plan": localized_report_label("Research Team Decision"),
                "trader_investment_plan": localized_report_label("Trading Team Plan"),
                "final_trade_decision": localized_report_label("Portfolio Management Decision"),
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports - use .get() to handle missing sections
        analyst_sections = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append(f"## {localized_report_label('Analyst Team Reports')}")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### {localized_report_label('Market Analysis')}\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### {localized_report_label('Social Sentiment')}\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### {localized_report_label('News Analysis')}\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### {localized_report_label('Fundamentals Analysis')}\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections.get("investment_plan"):
            report_parts.append(f"## {localized_report_label('Research Team Decision')}")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append(f"## {localized_report_label('Trading Team Plan')}")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections.get("final_trade_decision"):
            report_parts.append(f"## {localized_report_label('Portfolio Management Decision')}")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout

def format_tokens(n):
    """Format token count for display."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def format_message_preview(content, max_length=160) -> str:
    """Return a bounded single-line preview for the live terminal table."""
    if not content:
        return ""
    preview = re.sub(r"\s+", " ", str(content)).strip()
    if len(preview) > max_length:
        return preview[: max_length - 3].rstrip() + "..."
    return preview


def get_messages_panel_capacity() -> int:
    """Estimate how many single-line rows fit in the live messages panel."""
    size = console.size
    terminal_height = getattr(size, "height", size[1] if isinstance(size, tuple) else 24)
    upper_height = max(8, int(max(1, terminal_height - 3) * 3 / 8))
    usable_table_height = upper_height - 7
    return max(3, min(6, usable_table_height // 2))


def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team - filter to only include agents in agent_status
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Filter teams to only include agents that are in agent_status
    teams = {}
    for team, agents in all_teams.items():
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team. Keep it shorter than the fixed
        # cell width so Rich never truncates it with an ellipsis.
        progress_table.add_row("─" * 10, "─" * 10, "─" * 10, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=True, ratio=1
    )  # Keep each live-preview row to one line so Live height stays stable.

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = format_message_preview(content)
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp descending (newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # Calculate how many messages we can show based on available space
    max_messages = get_messages_panel_capacity()

    # Get the first N messages (newest ones)
    recent_messages = all_messages[:max_messages]

    # Add messages to table (already in newest-first order)
    for timestamp, msg_type, content in recent_messages:
        wrapped_content = Text(content, overflow="ellipsis", no_wrap=True)
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Agent progress - derived from agent_status dict
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # Report progress - based on agent completion (not just content existence)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # Build stats parts
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM and tool stats from callback handler
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token display with graceful fallback
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # Elapsed time
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", "r", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]BigA-Analysis-Agents: China A-share Multi-Agent Analysis CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Forked from TauricResearch/TradingAgents and adapted for China A-share research[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to BigA-Analysis-Agents",
        subtitle="China A-share Multi-Agent Trading Analysis",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the Tushare ts_code to analyze (examples: 000001.SZ, 600000.SH, 300750.SZ)",
            "000001.SZ",
        )
    )
    selected_ticker = get_ticker()

    # Step 2: Optional Eastmoney Guba social sentiment setup
    console.print(
        create_question_box(
            "Step 2: Community Sentiment",
            "Optionally authorize Eastmoney Guba as a Social Analyst data source. The actual collection runs later inside the social analysis workflow.",
            "No",
        )
    )
    social_monitor_result = setup_eastmoney_guba_for_analysis(selected_ticker)

    # Step 3: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 3: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 4: Output language
    console.print(
        create_question_box(
            "Step 4: Output Language",
            "Select the language for analyst reports and final decision"
        )
    )
    output_language = ask_output_language()

    # Step 5: Select analysts
    console.print(
        create_question_box(
            "Step 5: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 6: Research depth
    console.print(
        create_question_box(
            "Step 6: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # Step 7: LLM Provider
    console.print(
        create_question_box(
            "Step 7: LLM Provider", "Select your LLM provider"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()

    # Step 8: Thinking agents
    console.print(
        create_question_box(
            "Step 8: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 9: Provider-specific thinking configuration
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        console.print(
            create_question_box(
                "Step 9: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print(
            create_question_box(
                "Step 9: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print(
            create_question_box(
                "Step 9: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
        "social_monitor_result": social_monitor_result,
    }


def setup_eastmoney_guba_for_analysis(ts_code: str) -> dict:
    """Optionally open Eastmoney Guba login for later Social Analyst collection."""
    use_guba = select_with_research_depth_style(
        "是否启用东方财富股吧社区情绪链路？",
        choices=[
            questionary.Choice("是，使用 Chrome 浏览器打开股吧网页，需要您手动登录并完成人机验证", value=True),
            questionary.Choice("否，暂不需要，不影响后续分析流程", value=False),
        ],
        qmark="",
        show_instruction=False,
    )
    if not use_guba:
        return {"enabled": False, "status": "skipped"}

    try:
        browser_handle = _open_eastmoney_guba_login_browser(ts_code)
    except Exception as exc:
        _handle_social_browser_unavailable(exc)
        return {"enabled": False, "status": "browser_unavailable", "error": str(exc)}

    choice = _prompt_after_eastmoney_login()
    if choice == "skip":
        _close_social_browser(browser_handle)
        console.print("[yellow]本次调研暂不使用东方财富股吧社区数据。[/yellow]")
        return {"enabled": False, "status": "login_skipped"}
    if choice == "exit" or choice is None:
        _close_social_browser(browser_handle)
        console.print("[yellow]已退出本次分析流程。[/yellow]")
        raise typer.Exit(code=0)

    cdp_url = browser_handle.get("cdp_url", "")
    os.environ["SOCIAL_MONITOR_ENABLED"] = "true"
    os.environ["SOCIAL_MONITOR_SOURCES"] = "eastmoney_guba"
    os.environ["SOCIAL_MONITOR_COLLECT_DURING_ANALYSIS"] = "true"
    if cdp_url:
        os.environ["SOCIAL_BROWSER_CDP_URL"] = cdp_url

    console.print(
        "[green]已完成东方财富股吧登录。[/green]"
        "Chrome 浏览器将保持打开，后续由 Social Analyst 工具统一采集并分析股吧社区数据。"
    )
    return {
        "enabled": True,
        "status": "login_confirmed",
        "browser_handle": browser_handle,
        "cdp_url": cdp_url,
    }


def _prompt_after_eastmoney_login() -> str:
    console.print(
        "\n[bold cyan]Chrome 浏览器已打开东方财富股吧页面。[/bold cyan]\n"
        "请在浏览器中手动登录股吧账号并完成人机验证，然后回到本终端选择下一步："
    )
    return select_with_research_depth_style(
        "选择下一步操作：",
        choices=[
            questionary.Choice("我已完成登录，保持浏览器打开并进入下一步配置", value="done"),
            questionary.Choice("跳过本次东财股吧数据，继续开始调研分析", value="skip"),
            questionary.Choice("退出本进程", value="exit"),
        ],
        qmark="",
        show_instruction=False,
    )


def _open_eastmoney_guba_login_browser(ts_code: str):
    from playwright.sync_api import sync_playwright
    from tradingagents.dataflows.social_monitor.browser_collector import profile_dir
    from tradingagents.dataflows.social_monitor.sources import EASTMONEY_GUBA, source_url

    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    target_url = source_url(ts_code, EASTMONEY_GUBA)

    existing = _reuse_existing_social_browser(profile, target_url)
    if existing:
        console.print(f"[cyan]复用已打开的 Chrome 股吧页面。Profile: {profile}[/cyan]")
        return existing

    with sync_playwright() as playwright:
        executable_path = _resolve_browser_executable_path(playwright)

    port = _find_free_port()
    cdp_url = f"http://127.0.0.1:{port}"
    cmd = [
        executable_path,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        target_url,
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    handle = {"process": process, "cdp_url": cdp_url, "profile": str(profile), "owned": True}
    try:
        _wait_for_cdp_endpoint(cdp_url, process)
    except Exception:
        _close_social_browser(handle)
        raise
    _write_social_browser_session(profile, process.pid, cdp_url)
    console.print(f"[cyan]Chrome 浏览器已打开股吧网页。Profile: {profile}[/cyan]")
    return handle


def _reuse_existing_social_browser(profile: Path, target_url: str) -> dict | None:
    for cdp_url in _existing_social_browser_cdp_candidates(profile):
        if not _cdp_endpoint_ready(cdp_url):
            continue
        _open_url_in_existing_cdp(cdp_url, target_url)
        _write_social_browser_session(profile, None, cdp_url)
        return {"process": None, "cdp_url": cdp_url, "profile": str(profile), "owned": False}
    return None


def _existing_social_browser_cdp_candidates(profile: Path) -> list[str]:
    candidates: list[str] = []
    env_url = os.getenv("SOCIAL_BROWSER_CDP_URL", "").strip()
    if env_url:
        candidates.append(env_url)

    session_file = profile / SOCIAL_BROWSER_SESSION_FILE
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
        cdp_url = str(data.get("cdp_url", "")).strip()
        if cdp_url:
            candidates.append(cdp_url)
    except Exception:
        pass

    devtools_port_file = profile / "DevToolsActivePort"
    try:
        port = devtools_port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        if port:
            candidates.append(f"http://127.0.0.1:{port}")
    except Exception:
        pass

    candidates.extend(_running_social_browser_cdp_candidates(profile))
    return list(dict.fromkeys(candidates))


def _running_social_browser_cdp_candidates(profile: Path) -> list[str]:
    try:
        output = subprocess.check_output(["ps", "-axo", "command="], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []

    profile_text = str(profile)
    candidates: list[str] = []
    for line in output.splitlines():
        if "--remote-debugging-port=" not in line or "--user-data-dir=" not in line:
            continue
        if profile_text not in line:
            continue
        match = re.search(r"--remote-debugging-port=(\d+)", line)
        if match:
            candidates.append(f"http://127.0.0.1:{match.group(1)}")
    return candidates


def _cdp_endpoint_ready(cdp_url: str) -> bool:
    try:
        with urlopen(f"{cdp_url}/json/version", timeout=0.5) as response:
            return response.status == 200
    except Exception:
        return False


def _open_url_in_existing_cdp(cdp_url: str, target_url: str) -> None:
    encoded = quote(target_url, safe=":/,?&=.%")
    request = Request(f"{cdp_url}/json/new?{encoded}", method="PUT")
    try:
        with urlopen(request, timeout=1.5):
            return
    except Exception:
        return


def _write_social_browser_session(profile: Path, pid: int | None, cdp_url: str) -> None:
    try:
        payload = {"pid": pid, "cdp_url": cdp_url, "updated_at": time.time()}
        (profile / SOCIAL_BROWSER_SESSION_FILE).write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _clear_social_browser_session(profile: str | Path | None, cdp_url: str | None = None) -> None:
    if not profile:
        return
    session_file = Path(profile) / SOCIAL_BROWSER_SESSION_FILE
    try:
        if cdp_url:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            if data.get("cdp_url") != cdp_url:
                return
        session_file.unlink()
    except Exception:
        pass


def _resolve_browser_executable_path(playwright) -> str:
    for candidate in _browser_executable_candidates(playwright):
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    if _install_playwright_chromium():
        for candidate in _browser_executable_candidates(playwright):
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if path.exists() and path.is_file() and os.access(path, os.X_OK):
                return str(path)
    raise RuntimeError(
        "未找到可启动的 Chrome 浏览器。`pip install -e .` 只会安装 Python 包，"
        "不会保证浏览器运行时已下载。请安装本机 Google Chrome，或在当前虚拟环境执行 `python -m playwright install chromium`，"
        "或 `python -m cli.install_runtime_deps`；"
        "或设置 SOCIAL_BROWSER_EXECUTABLE_PATH 指向本机 Chrome 可执行文件。"
    )


def _install_playwright_chromium() -> bool:
    if os.getenv("SOCIAL_BROWSER_AUTO_INSTALL", "true").strip().lower() in {"0", "false", "no"}:
        return False
    console.print("[yellow]未检测到可用的浏览器运行时，正在安装 Playwright 浏览器运行时...[/yellow]")
    from cli.install_runtime_deps import install_chromium

    if install_chromium(quiet=True):
        return True
    console.print(
        "[yellow]Playwright 浏览器运行时自动安装失败。请安装本机 Google Chrome，或设置 "
        "SOCIAL_BROWSER_EXECUTABLE_PATH。[/yellow]"
    )
    return False


def _browser_executable_candidates(playwright) -> list[str]:
    candidates: list[str] = []
    configured = os.getenv("SOCIAL_BROWSER_EXECUTABLE_PATH", "").strip()
    if configured:
        candidates.append(configured)
    try:
        candidates.append(str(playwright.chromium.executable_path))
    except Exception:
        pass
    candidates.extend(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "~/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    )
    for binary in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(binary)
        if found:
            candidates.append(found)
    return candidates


def _close_social_browser(handle: dict | None) -> None:
    if not handle:
        return
    context = handle.get("context")
    playwright = handle.get("playwright")
    try:
        if context:
            context.close()
    except Exception:
        pass
    try:
        if playwright:
            playwright.stop()
    except Exception:
        pass
    process = handle.get("process")
    if process and process.poll() is None:
        if handle.get("owned") is False:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()
    if handle.get("owned", True):
        _clear_social_browser_session(handle.get("profile"), handle.get("cdp_url"))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_cdp_endpoint(cdp_url: str, process: subprocess.Popen, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Chrome 浏览器启动后立即退出，无法建立调试连接。")
        try:
            with urlopen(f"{cdp_url}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Chrome 浏览器已启动，但调试端口未就绪：{last_error}")


def _register_social_browser_cleanup(handle: dict | None) -> None:
    if not handle:
        return

    def cleanup() -> None:
        _close_social_browser(handle)

    atexit.register(cleanup)


def _handle_social_browser_unavailable(exc: Exception) -> None:
    message = str(exc)
    console.print(
        "[yellow]未能使用 Chrome 浏览器打开东方财富股吧页面。[/yellow]\n"
        f"[dim]{message[:500]}[/dim]\n"
        "如果已执行过 `pip install -e .`，还需要确保本机 Chrome 或浏览器运行时可用：\n"
        "[bold]python -m cli.install_runtime_deps[/bold]\n"
        "[bold]./start.sh[/bold]\n"
        "或设置 SOCIAL_BROWSER_EXECUTABLE_PATH 指向本机 Chrome 可执行文件。\n"
        "本次调研可以暂不使用东方财富股吧社区数据。"
    )
    choice = select_with_research_depth_style(
        "请选择：",
        choices=[
            questionary.Choice("继续开始调研分析，不使用东财股吧社区数据", value="continue"),
            questionary.Choice("退出本进程", value="exit"),
        ],
        qmark="",
        show_instruction=False,
    )
    if choice != "continue":
        raise typer.Exit(code=0)


def _print_social_collection_result(rows: list[dict]) -> None:
    table = Table(title="Eastmoney Guba Collection", box=box.SIMPLE_HEAVY)
    for col in ("source", "ts_code", "status", "posts_seen", "posts_inserted", "error"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("source", "")),
            str(row.get("ts_code", "")),
            str(row.get("status", "")),
            str(row.get("posts_seen", "")),
            str(row.get("posts_inserted", "")),
            str(row.get("error", ""))[:120],
        )
    console.print(table)
    if rows and all(row.get("status") in {"error", "no_data"} for row in rows):
        console.print(
            "[yellow]未采集到可用东财股吧帖子。完整分析仍会继续，并回退到 iFinD/Tushare/news proxy 等信号。[/yellow]"
        )


def get_ticker():
    """Get ticker symbol from user input."""
    from tradingagents.dataflows.a_share_utils import validate_ts_code

    while True:
        ticker = typer.prompt("", default="000001.SZ")
        try:
            return validate_ts_code(ticker)
        except ValueError as exc:
            console.print(f"[red]Error: {exc}[/red]")


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save complete analysis report to disk with organized subfolders."""
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append((localized_report_label("Market Analyst"), final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append((localized_report_label("Social Analyst"), final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append((localized_report_label("News Analyst"), final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append((localized_report_label("Fundamentals Analyst"), final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. {localized_report_label('Analyst Team Reports')}\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append((localized_report_label("Bull Researcher"), debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append((localized_report_label("Bear Researcher"), debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append((localized_report_label("Research Manager"), debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. {localized_report_label('Research Team Decision')}\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(
            f"## III. {localized_report_label('Trading Team Plan')}\n\n"
            f"### {localized_report_label('Trader')}\n{final_state['trader_investment_plan']}"
        )

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append((localized_report_label("Aggressive Analyst"), risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append((localized_report_label("Conservative Analyst"), risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append((localized_report_label("Neutral Analyst"), risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. {localized_report_label('Risk Management Team Decision')}\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(
                f"## V. {localized_report_label('Portfolio Manager Decision')}\n\n"
                f"### {localized_report_label('Portfolio Manager')}\n{risk['judge_decision']}"
            )

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")
    return save_path / "complete_report.md"


def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append((localized_report_label("Market Analyst"), final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append((localized_report_label("Social Analyst"), final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append((localized_report_label("News Analyst"), final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append((localized_report_label("Fundamentals Analyst"), final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel(f"[bold]I. {localized_report_label('Analyst Team Reports')}[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append((localized_report_label("Bull Researcher"), debate["bull_history"]))
        if debate.get("bear_history"):
            research.append((localized_report_label("Bear Researcher"), debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append((localized_report_label("Research Manager"), debate["judge_decision"]))
        if research:
            console.print(Panel(f"[bold]II. {localized_report_label('Research Team Decision')}[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel(f"[bold]III. {localized_report_label('Trading Team Plan')}[/bold]", border_style="yellow"))
        console.print(Panel(Markdown(final_state["trader_investment_plan"]), title=localized_report_label("Trader"), border_style="blue", padding=(1, 2)))

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append((localized_report_label("Aggressive Analyst"), risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append((localized_report_label("Conservative Analyst"), risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append((localized_report_label("Neutral Analyst"), risk["neutral_history"]))
        if risk_reports:
            console.print(Panel(f"[bold]IV. {localized_report_label('Risk Management Team Decision')}[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel(f"[bold]V. {localized_report_label('Portfolio Manager Decision')}[/bold]", border_style="green"))
            console.print(Panel(Markdown(risk["judge_decision"]), title=localized_report_label("Portfolio Manager"), border_style="blue", padding=(1, 2)))


def update_research_team_status(status):
    """Update status for research team members (not Trader)."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# Ordered list of analysts for status transitions
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def update_analyst_statuses(message_buffer, chunk):
    """Update analyst statuses based on accumulated report state.

    Logic:
    - Store new report content from the current chunk if present
    - Check accumulated report_sections (not just current chunk) for status
    - Analysts with reports = completed
    - First analyst without report = in_progress
    - Remaining analysts without reports = pending
    - When all analysts done, set Bull Researcher to in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # Capture new report content from current chunk
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # Determine status from accumulated sections, not just current chunk
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # When all analysts complete, transition research team to in_progress
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")

def extract_content_string(content):
    """Extract string content from various message formats.
    Returns None if no meaningful text content is found.
    """
    import ast

    def is_empty(val):
        """Check if value is empty using Python's truthiness."""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # Can't parse = real text
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


def classify_message_type(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content.

    Returns:
        (type, content) - type is one of: User, Agent, Data, Control
                        - content is extracted string or None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, 'content', None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # Fallback for unknown types
    return ("System", content)


def format_tool_args(args, max_length=80) -> str:
    """Format tool arguments for terminal display."""
    return format_message_preview(args, max_length=max_length)

def run_analysis(checkpoint: bool = False):
    # First get all user selections
    selections = get_user_selections()
    social_browser_handle = selections.get("social_monitor_result", {}).get("browser_handle")
    if social_browser_handle:
        _register_social_browser_cleanup(social_browser_handle)

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # Provider-specific thinking configuration
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    config["checkpoint_enabled"] = checkpoint

    # Create stats callback handler for tracking LLM/tool calls
    stats_handler = StatsCallbackHandler()

    # Normalize analyst selection to predefined order (selection is a 'set', order is fixed)
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    # Initialize the graph with callbacks bound to LLMs
    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    # Initialize message buffer with selected analysts
    message_buffer.init_for_analysis(selected_analyst_keys)

    # Track start time for elapsed display
    start_time = time.time()

    # Create result directory
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper
    
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # Now start the display layout
    layout = create_layout()
    with Live(
        layout,
        refresh_per_second=4,
        vertical_overflow="crop",
    ) as live:
        def refresh_display(spinner_text=None):
            update_display(
                layout,
                spinner_text=spinner_text,
                stats_handler=stats_handler,
                start_time=start_time,
            )

        # Initial display
        refresh_display()

        # Add initial messages
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        message_buffer.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        refresh_display()

        # Update agent status to in_progress for the first analyst
        first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")
        refresh_display()

        # Create spinner text
        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        refresh_display(spinner_text)

        # Initialize state and get graph args with callbacks
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"], selections["analysis_date"]
        )
        # Pass callbacks to graph config for tool execution tracking
        # (LLM tracking is handled separately via LLM constructor)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # Stream the analysis
        trace = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            # Process all messages in chunk, deduplicating by message ID
            for message in chunk.get("messages", []):
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in message_buffer._processed_message_ids:
                        continue
                    message_buffer._processed_message_ids.add(msg_id)

                msg_type, content = classify_message_type(message)
                if content and content.strip():
                    message_buffer.add_message(msg_type, content)

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(tool_call["name"], tool_call["args"])
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

            # Update analyst statuses based on report state (runs on every chunk)
            update_analyst_statuses(message_buffer, chunk)

            # Research Team - Handle Investment Debate State
            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                # Only update status when there's actual content
                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### {localized_report_label('Bull Researcher Analysis')}\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### {localized_report_label('Bear Researcher Analysis')}\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### {localized_report_label('Research Manager Decision')}\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            # Trading Team
            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            # Risk Management Team - Handle Risk Debate State
            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### {localized_report_label('Aggressive Analyst Analysis')}\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### {localized_report_label('Conservative Analyst Analysis')}\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### {localized_report_label('Neutral Analyst Analysis')}\n{neu_hist}"
                    )
                if judge:
                    if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                        message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### {localized_report_label('Portfolio Manager Decision')}\n{judge}"
                        )
                        message_buffer.update_agent_status("Aggressive Analyst", "completed")
                        message_buffer.update_agent_status("Conservative Analyst", "completed")
                        message_buffer.update_agent_status("Neutral Analyst", "completed")
                        message_buffer.update_agent_status("Portfolio Manager", "completed")

            # Update the display
            refresh_display()

            trace.append(chunk)

        # Get final state and decision
        final_state = trace[-1]
        decision = graph.process_signal(final_state["final_trade_decision"])

        # Update all agent statuses to completed
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )

        # Update final report sections
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        refresh_display()

    if social_browser_handle:
        _close_social_browser(social_browser_handle)
        social_browser_handle = None
        os.environ.pop("SOCIAL_BROWSER_CDP_URL", None)

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # Prompt to save report
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # Prompt to display full report
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


@app.command()
def analyze(
    checkpoint: bool = typer.Option(
        False,
        "--checkpoint",
        help="Enable checkpoint/resume: save state after each node so a crashed run can resume.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
):
    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints
        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
    run_analysis(checkpoint=checkpoint)


@app.command()
def social_login(
    symbol: str = typer.Option(
        "300750.SZ",
        "--symbol",
        help="Tushare ts_code whose Eastmoney Guba page should be opened.",
    ),
):
    """Open a persistent browser profile for manual Eastmoney Guba login."""
    from tradingagents.dataflows.a_share_utils import validate_ts_code

    ts_code = validate_ts_code(symbol)
    console.print(
        "[cyan]Opening a persistent Eastmoney Guba browser profile.[/cyan]\n"
        "Log in manually. Cookies stay in your local profile only."
    )
    handle = _open_eastmoney_guba_login_browser(ts_code)
    try:
        questionary.select(
            "登录完成后回到终端选择：",
            choices=[questionary.Choice("我已完成登录，关闭浏览器", value="done")],
        ).ask()
    finally:
        _close_social_browser(handle)


@app.command()
def social_monitor(
    symbols: str = typer.Option(
        ...,
        "--symbols",
        help="Comma-separated Tushare ts_codes, e.g. 300750.SZ,000001.SZ.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run one collection pass and exit. Used by default when --loop is not set.",
    ),
    loop: bool = typer.Option(
        False,
        "--loop",
        help="Run continuously using SOCIAL_MONITOR_INTERVAL_SECONDS.",
    ),
    sources: str = typer.Option(
        "",
        "--sources",
        help="Comma-separated sources: eastmoney_guba. Defaults to env/config.",
    ),
    scroll_seconds: int = typer.Option(
        90,
        "--scroll-seconds",
        help="Seconds to scroll/listen per source and symbol.",
    ),
    max_posts: int = typer.Option(
        0,
        "--max-posts",
        help="Maximum posts per symbol/source. Defaults to SOCIAL_MONITOR_MAX_POSTS_PER_SYMBOL.",
    ),
    max_pages: int = typer.Option(
        0,
        "--max-pages",
        help="Maximum pages per symbol/source for paginated sources. Defaults to SOCIAL_MONITOR_MAX_PAGES.",
    ),
    headed: bool = typer.Option(
        False,
        "--headed",
        help="Show browser during collection. Useful for diagnosing login or verification pages.",
    ),
):
    """Collect Eastmoney Guba forum posts using an authorized browser session."""
    from tradingagents.dataflows.a_share_utils import validate_ts_code
    from tradingagents.dataflows.social_monitor.runner import collect_loop, collect_once
    from tradingagents.dataflows.social_monitor.sources import parse_sources

    symbol_list = [validate_ts_code(item.strip()) for item in symbols.split(",") if item.strip()]
    if not symbol_list:
        raise typer.BadParameter("At least one symbol is required.")
    source_list = parse_sources(sources) if sources else None
    max_posts_value = max_posts if max_posts > 0 else None
    max_pages_value = max_pages if max_pages > 0 else None
    if loop:
        console.print(f"[cyan]Starting social monitor loop for {', '.join(symbol_list)}.[/cyan]")
        collect_loop(
            symbol_list,
            source_list,
            scroll_seconds=scroll_seconds,
            max_posts_per_symbol=max_posts_value,
            headless=not headed,
            max_pages_per_symbol=max_pages_value,
        )
        return

    console.print(f"[cyan]Running one social monitor pass for {', '.join(symbol_list)}.[/cyan]")
    rows = collect_once(
        symbol_list,
        source_list,
        scroll_seconds=scroll_seconds,
        max_posts_per_symbol=max_posts_value,
        headless=not headed,
        max_pages_per_symbol=max_pages_value,
    )
    table = Table(title="Social Monitor Results", box=box.SIMPLE_HEAVY)
    for col in ("source", "ts_code", "status", "posts_seen", "posts_inserted", "error"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("source", "")),
            str(row.get("ts_code", "")),
            str(row.get("status", "")),
            str(row.get("posts_seen", "")),
            str(row.get("posts_inserted", "")),
            str(row.get("error", ""))[:120],
        )
    console.print(table)


@app.command()
def ifind_smoke(
    symbol: str = typer.Option(
        "300750.SZ",
        "--symbol",
        help="Tushare ts_code to query through iFinD QuantAPI.",
    ),
):
    """Run a read-only iFinD QuantAPI connectivity smoke test."""
    from tradingagents.dataflows.a_share_utils import validate_ts_code
    from tradingagents.dataflows import ifind_provider

    ts_code = validate_ts_code(symbol)
    console.print(Markdown(ifind_provider.status()))
    if not ifind_provider.is_enabled():
        console.print("[yellow]iFinD is disabled by IFIND_ENABLED=false.[/yellow]")
        return
    if not ifind_provider.has_credentials():
        console.print(
            "[yellow]No iFinD token found. Set IFIND_ACCESS_TOKEN and/or IFIND_REFRESH_TOKEN in .env.[/yellow]"
        )
        return
    console.print(Markdown(ifind_provider.real_time_quote(ts_code)))
    console.print(Markdown(ifind_provider.popularity_signal(ts_code)))


if __name__ == "__main__":
    app()
