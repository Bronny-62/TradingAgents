from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tradingagents.agents.utils.a_share_tools import (
    get_a_share_announcements,
    get_a_share_company_profile,
    get_a_share_financials,
    get_a_share_fundamental_snapshot,
    get_a_share_hotness,
    get_a_share_indicators,
    get_a_share_market_snapshot,
    get_a_share_moneyflow,
    get_a_share_ohlcv,
    get_a_share_realtime_news,
    get_a_share_social_sentiment,
    get_cn_macro_news,
    get_social_monitoring_coverage,
    search_a_share_news,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every user-visible report section so saved reports and the
    terminal display stay in the selected language.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}, including headings, labels, and speaker names."


def is_chinese_output_language() -> bool:
    from tradingagents.dataflows.config import get_config

    lang = get_config().get("output_language", "English").strip().lower()
    return lang in {"chinese", "中文", "zh", "zh-cn", "zh_cn"}


def build_instrument_context(ticker: str) -> str:
    """Describe the exact A-share instrument so agents preserve ts_code."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact Tushare ts_code in every tool call, report, and recommendation. "
        "Valid A-share examples include `000001.SZ`, `600000.SH`, and `300750.SZ`."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        placeholder = HumanMessage(content="Continue")

        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), placeholder]}

    return delete_messages

