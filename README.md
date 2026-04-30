# BigA-Analysis-Agents

[English](README.md) | [中文](README.zh-CN.md)

BigA-Analysis-Agents is a China A-share focused fork of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents).
It keeps the original multi-agent research workflow, but adapts the data
sources, prompts, CLI flow, and final reports for China A-share analysis.

This project is for research and decision support only. It does not place
orders, does not guarantee returns, and is not investment, financial, or trading
advice.

## Screenshots

The README expects real usage screenshots under `docs/images/`. The three
uploaded screenshots should use these filenames:

- `docs/images/cli-setup.png`
- `docs/images/portfolio-decision.png`
- `docs/images/market-report.png`

![CLI setup flow](docs/images/cli-setup.png)

![Portfolio manager decision](docs/images/portfolio-decision.png)

![Market analyst report](docs/images/market-report.png)

## A-Share Adaptations

BigA-Analysis-Agents analyzes symbols in Tushare `ts_code` format, for example
`000001.SZ`, `600000.SH`, or `300750.SZ`.

Compared with the upstream US-market workflow, this fork focuses on:

- China A-share symbol normalization and CSI 300 oriented reflection.
- Tushare daily bars, valuation, money flow, limit-up/limit-down, financial
  tables, and hotness-style signals.
- Optional iFinD QuantAPI real-time quote and popularity-style enrichment.
- OpenNews and Jin10 MCP news search for China market context.
- Cninfo/Juchao announcement lookup for listed-company disclosures.
- Eastmoney Guba browser-session social monitoring with manual login.
- Chinese output synchronization across analyst, research, trader, risk, and
  portfolio-manager modules.
- Terminal rendering tuned for long, table-heavy A-share reports.

The active analyst agents no longer expose the upstream US-market tool schemas
such as Yahoo Finance, Reddit, insider transactions, or SPY alpha.

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

## Data Sources

You need to apply for and manage your own data-source accounts. This repository
does not include or redistribute third-party market data, credentials, browser
cookies, local caches, or exported reports.

| Channel | Source | Used For | Credential |
| --- | --- | --- | --- |
| Market | Tushare Pro | OHLCV, valuation, money flow, limit data, financial tables | `TUSHARE_TOKEN` |
| Market / Social | iFinD QuantAPI | Optional quote and enrichment signals | `IFIND_ACCESS_TOKEN`, `IFIND_REFRESH_TOKEN` |
| News | OpenNews MCP / REST | News search and market context | `OPENNEWS_TOKEN` |
| News fallback | Jin10 MCP | Flash/news fallback | `JIN10_MCP_TOKEN` |
| Fundamentals | Cninfo / Juchao WebAPI | Announcements and disclosure links | usually no local API key |
| Social | Eastmoney Guba | Forum monitoring through authorized browser session | local browser login |
| Social optional | Xueqiu | Experimental browser-session monitoring | local browser login |

The social monitor does not implement captcha bypass, proxy pools, fingerprint
spoofing, credential sharing, or anti-bot circumvention. If a site requires
verification or blocks automation, collection fails gracefully and the analysis
continues with available signals.

## Installation

Python 3.13 is recommended.

```bash
git clone https://github.com/Bronny-62/TradingAgents.git
cd TradingAgents
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Recommended launch sequence:

```bash
source .venv/bin/activate
pip install -e .
./start.sh
```

On Windows:

```bat
.venv\Scripts\activate
pip install -e .
start.bat
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
SOCIAL_MONITOR_SOURCES=eastmoney_guba,xueqiu
```

LLM provider keys are also configured through `.env`, for example
`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ANTHROPIC_API_KEY`,
or other supported providers.

Never commit `.env`, browser profiles, SQLite databases, JSONL caches, exported
reports, cookies, HAR files, or trace archives.

## Usage

Start the interactive CLI:

```bash
python -m cli.main analyze
```

Then enter an A-share `ts_code`, for example `300750.SZ`.

During the community sentiment step, you can enable Eastmoney Guba monitoring.
If enabled, Chrome opens the stock's Guba page. Log in manually, complete any
verification, return to the terminal, and continue the analysis.

Useful commands:

```bash
# Start with runtime checks
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
