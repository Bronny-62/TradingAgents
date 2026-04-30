# BigA-Analysis-Agents

[English](README.md) | [中文](README.zh-CN.md)

BigA-Analysis-Agents is a China A-share focused fork of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents).
It keeps the original multi-agent research workflow, but adapts the data
sources, prompts, CLI flow, and final reports for China A-share analysis.

This project is for research and decision support only. It does not place
orders, does not guarantee returns, and is not investment, financial, or trading
advice.

## A-Share Adaptations

BigA-Analysis-Agents analyzes symbols in Tushare `ts_code` format, for example
`000001.SZ`, `600000.SH`, or `300750.SZ`.

Compared with the upstream US-market workflow, this fork focuses on:

- China A-share symbol normalization and CSI 300 oriented reflection.
- Tushare daily bars, valuation, money flow, limit-up/limit-down, financial
  tables, announcements, and hotness-style signals.
- Optional iFinD QuantAPI real-time quote and popularity-style enrichment.
- OpenNews with Jin10 fallback for China market news and flash context.
- Cninfo/Juchao fallback lookup for listed-company announcements.
- Eastmoney Guba browser-session social monitoring with manual login, plus
  Tushare hotness and optional iFinD popularity signals.
- Chinese output synchronization across analyst, research, trader, risk, and
  portfolio-manager modules.
- Terminal rendering tuned for long, table-heavy A-share reports.

The active analyst agents no longer expose the upstream US-market tool schemas
such as Yahoo Finance, Reddit, insider transactions, or SPY alpha.

## Data Sources

You need to apply for and manage your own data-source accounts. This repository
does not include or redistribute third-party market data, credentials, browser
cookies, local caches, or exported reports.

| Channel | Sources | Used For | Official Link / API Entry |
| --- | --- | --- | --- |
| Market | Tushare Pro + optional iFinD QuantAPI | OHLCV, valuation, money flow, limit data, technical indicators, and optional real-time quote enrichment | [Tushare Pro](https://tushare.pro), [Tushare token guide](https://tushare.pro/document/1?doc_id=39), [iFinD API examples](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html), [iFinD help center](https://ftwc.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center.html) |
| News | OpenNews MCP / REST + Jin10 MCP fallback | News search, market context, macro/policy context, and flash/news fallback | [OpenNews token portal](https://6551.io/mcp), [OpenNews MCP docs](https://github.com/6551Team/opennews-mcp/blob/main/docs/README_ZH.md), [Jin10 MCP docs](https://mcp.jin10.com/app/doc.html) |
| Fundamentals | Tushare Pro + Cninfo/Juchao fallback for announcements | Company profile, financial statements, financial indicators, dividends, share float, forecasts, express reports, and announcements | [Tushare Pro](https://tushare.pro), [Tushare token guide](https://tushare.pro/document/1?doc_id=39), [Cninfo](https://www.cninfo.com.cn/), [Cninfo WebAPI](https://webapi.cninfo.com.cn/#/apiDoc) |
| Social | Eastmoney Guba + Tushare hotness + optional iFinD popularity signals | Authorized forum posts, Tushare `dc_hot` / `ths_hot`, and optional iFinD smart-stock-picking popularity signals | [Eastmoney Guba](https://guba.eastmoney.com.cn/), [Tushare Pro](https://tushare.pro), [Tushare token guide](https://tushare.pro/document/1?doc_id=39), [iFinD API examples](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html), [iFinD help center](https://ftwc.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center.html) |

The social monitor does not implement captcha bypass, proxy pools, fingerprint
spoofing, credential sharing, or anti-bot circumvention. If a site requires
verification or blocks automation, collection fails gracefully and the analysis
continues with available signals.

## Report Modules

A complete analysis report is organized into five sections:

1. **Analyst Team Reports**
   Market, social sentiment, news, and fundamentals analysis.
2. **Research Team Decision**
   Bull researcher, bear researcher, and research manager debate summary.
3. **Trading Team Plan**
   Trader action plan based on the research manager's investment plan.
4. **Risk Management Team Decision**
   Aggressive, conservative, and neutral risk analyst debate.
5. **Portfolio Manager Decision**
   Final portfolio-level decision and actionable risk controls.

## Usage Examples

Full sample output: [002837.SZ complete analysis report](reports/002837.SZ_20260430_173054/complete_report.md).

Interactive CLI startup and configuration flow.

![CLI startup flow](docs/images/cli-startup.png?raw=1&v=39002c22b441)

Portfolio manager final decision with portfolio-level action and risk controls.

![Portfolio manager decision](docs/images/portfolio-manager-decision.png?raw=1&v=14d188a5cab1)

## Installation

Python 3.13 is recommended.

```bash
git clone https://github.com/Bronny-62/BigA-Analysis-Agents.git
cd BigA-Analysis-Agents
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy the example environment file and fill in only the credentials you use:

```bash
cp .env.example .env
```

Core variables:

```env
TUSHARE_TOKEN=
OPENNEWS_TOKEN=
JIN10_MCP_TOKEN=
IFIND_ENABLED=true
IFIND_ACCESS_TOKEN=
IFIND_REFRESH_TOKEN=
SOCIAL_MONITOR_ENABLED=false
SOCIAL_MONITOR_SOURCES=eastmoney_guba
```

LLM provider keys are also configured through `.env`, for example
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ANTHROPIC_API_KEY`,
or other supported providers.

Never commit `.env`, browser profiles, SQLite databases, JSONL caches, exported
reports, cookies, HAR files, or trace archives.

## Usage Guide

Recommended one-click startup on macOS / Linux:

```bash
./start.sh
```

On Windows:

```bat
start.bat
```

The startup script performs runtime checks and enters the interactive analysis
flow. Then enter an A-share `ts_code`, for example `300750.SZ`.

During the community sentiment step, you can enable Eastmoney Guba monitoring.
If enabled, Chrome opens the stock's Guba page. Log in manually, complete any
verification, return to the terminal, and continue the analysis.

Useful commands:

```bash
# Recommended one-click startup on macOS / Linux
./start.sh

# iFinD connectivity smoke test
python -m cli.main ifind-smoke --symbol 300750.SZ

# Manual social login
python -m cli.main social-login --symbol 300750.SZ

# One-time social collection
python -m cli.main social-monitor --symbols 300750.SZ --once --sources eastmoney_guba
```

## Local State

Runtime state is written under `~/.tradingagents` by default:

- cached market/news/social data
- social monitor SQLite database
- browser profile for authorized forum monitoring
- memory log for prior decisions and reflections

These files are local-only and must not be published.

## Testing

```bash
python -m pytest -q
```

Targeted A-share dataflow tests:

```bash
python -m pytest tests/test_a_share_dataflows.py -q
```

## Attribution

This project is a derivative work of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents),
which is licensed under the Apache License 2.0. The upstream framework, agent
workflow, and portions of the original codebase remain credited to their
original authors.

## License

This repository is distributed under the Apache License 2.0. See
[LICENSE](LICENSE).

## Disclaimer

BigA-Analysis-Agents is for research, education, and personal analytical
workflow experiments only. Outputs may be incomplete, delayed, inaccurate, or
affected by model hallucination and third-party data-source availability. You
are solely responsible for verifying all information and for any investment or
trading decisions you make.
