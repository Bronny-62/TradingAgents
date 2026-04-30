# BigA-Analysis-Agents

[English](README.md) | [中文](README.zh-CN.md)

BigA-Analysis-Agents 是
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
面向中国 A 股市场改造的多智能体投研分析框架。它保留了原项目的多角色协作流程，并将数据源、提示词、CLI 交互和最终报告结构适配到 A 股分析场景。

本项目仅用于研究和决策辅助，不会自动下单，不承诺收益，也不构成投资、金融或交易建议。

## 使用截图

README 中的真实使用截图统一放在 `docs/images/` 目录。你上传的三张截图请使用以下文件名：

- `docs/images/cli-setup.png`
- `docs/images/portfolio-decision.png`
- `docs/images/market-report.png`

![CLI 配置流程](docs/images/cli-setup.png)

![投资组合经理最终决策](docs/images/portfolio-decision.png)

![市场分析师报告](docs/images/market-report.png)

## 面向 A 股市场的适配

BigA-Analysis-Agents 使用 Tushare `ts_code` 格式分析 A 股标的，例如
`000001.SZ`、`600000.SH`、`300750.SZ`。

相较于原始美股工作流，本项目主要做了以下适配：

- A 股代码规范化，并使用更符合本地市场的沪深 300 基准反思逻辑。
- 接入 Tushare 日线、估值、资金流、涨跌停、财务表、热度类信号。
- 可选接入 iFinD QuantAPI，补充实时行情和人气/选股类信号。
- 接入 OpenNews 和 Jin10 MCP，补充中国市场新闻与快讯背景。
- 接入巨潮资讯/Cninfo 公告查询，用于上市公司披露信息。
- 支持东方财富股吧本地浏览器会话监控，需要用户手动登录。
- 分析师、研究团队、交易员、风控团队、投资组合经理均支持所选输出语言同步。
- 终端 Live UI 针对长表格、多行工具结果和中文报告做了渲染稳定性优化。

当前活跃的 A 股分析智能体不再暴露原始美股工具链，例如 Yahoo Finance、Reddit、内部人交易或 SPY alpha 等。

## 完整报告模块

一次完整分析报告包含五个部分：

1. **分析师团队报告**
   包括市场、社媒情绪、新闻、基本面分析。
2. **研究团队决策**
   包括多方研究员、空方研究员和研究经理的辩论总结。
3. **交易团队计划**
   交易员根据研究经理投资计划给出交易行动方案。
4. **风险管理团队决策**
   包括激进、保守、中性三类风险分析师辩论。
5. **投资组合经理决策**
   给出最终组合级决策和风险控制建议。

## 数据源

你需要自行申请并管理数据源账号。本仓库不会包含或分发第三方行情数据、账号凭据、浏览器 Cookie、本地缓存或导出的分析报告。

| 模块 | 数据源 | 用途 | 凭据 |
| --- | --- | --- | --- |
| 市场 | Tushare Pro | K 线、估值、资金流、涨跌停、财务表 | `TUSHARE_TOKEN` |
| 市场 / 社媒 | iFinD QuantAPI | 可选实时行情和补充信号 | `IFIND_ACCESS_TOKEN`, `IFIND_REFRESH_TOKEN` |
| 新闻 | OpenNews MCP / REST | 新闻搜索与市场背景 | `OPENNEWS_TOKEN` |
| 新闻备用 | Jin10 MCP | 快讯和新闻备用链路 | `JIN10_MCP_TOKEN` |
| 基本面 | 巨潮资讯 / Cninfo WebAPI | 公告与披露链接 | 通常不需要本地 API key |
| 社媒 | 东方财富股吧 | 基于授权浏览器会话的论坛监控 | 本地浏览器登录 |
| 社媒可选 | 雪球 | 实验性的浏览器会话监控 | 本地浏览器登录 |

社媒监控不会绕过验证码，不使用代理池，不做浏览器指纹伪装，也不会共享凭据。如果平台要求验证或阻断自动化，采集器会记录结构化失败信息，主分析流程会继续使用其他可用信号。

## 安装

推荐使用 Python 3.13。

```bash
git clone https://github.com/Bronny-62/TradingAgents.git
cd TradingAgents
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

推荐启动方式：

```bash
source .venv/bin/activate
pip install -e .
./start.sh
```

Windows：

```bat
.venv\Scripts\activate
pip install -e .
start.bat
```

## 配置

复制示例环境变量文件，并只填写你实际使用的数据源和模型服务：

```bash
cp .env.example .env
```

核心变量：

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

LLM 服务的密钥也通过 `.env` 配置，例如 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、
`DASHSCOPE_API_KEY`、`ANTHROPIC_API_KEY` 或其他受支持的模型服务。

不要提交 `.env`、浏览器 profile、SQLite 数据库、JSONL 缓存、导出报告、Cookie、HAR 文件或 trace 归档。

## 使用

启动交互式 CLI：

```bash
python -m cli.main analyze
```

随后输入 A 股 `ts_code`，例如 `300750.SZ`。

在社区情绪配置步骤中，可以选择是否启用东方财富股吧监控。如果启用，系统会打开 Chrome 到对应股吧页面。你需要手动登录并完成必要验证，然后回到终端继续分析。

常用命令：

```bash
# 带运行时检查的启动方式
./start.sh

# iFinD 连通性检查
python -m cli.main ifind-smoke --symbol 300750.SZ

# 手动打开社媒登录浏览器
python -m cli.main social-login --symbol 300750.SZ

# 单次社媒采集
python -m cli.main social-monitor --symbols 300750.SZ --once --sources eastmoney_guba
```

## 本地状态

默认运行状态写入 `~/.tradingagents`：

- 行情、新闻、社媒等缓存
- 社媒监控 SQLite 数据库
- 用于授权论坛监控的浏览器 profile
- 历史决策和反思记忆日志

这些文件只应保留在本地，不应发布。

## 测试

```bash
python -m pytest -q
```

A 股数据流专项测试：

```bash
python -m pytest tests/test_a_share_dataflows.py -q
```

## 开源归属

本项目是
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
的衍生项目。原项目使用 Apache License 2.0。上游框架、智能体工作流和部分原始代码仍归属并致谢原作者。

## 许可证

本仓库使用 Apache License 2.0。详见 [LICENSE](LICENSE)。

## 免责声明

BigA-Analysis-Agents 仅用于研究、教育和个人分析流程实验。输出可能不完整、滞后、不准确，或受到模型幻觉和第三方数据源可用性的影响。你需要自行核验所有信息，并对自己的投资或交易决策负责。
