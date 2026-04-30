import asyncio
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.agents.utils.a_share_tools import (
    get_a_share_hotness,
    get_a_share_realtime_news,
    search_a_share_news,
)
from tradingagents.dataflows import cninfo_provider, ifind_provider, mcp_news_provider, social_provider, tushare_provider
from tradingagents.dataflows.a_share_utils import validate_ts_code
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.social_monitor import browser_collector
from tradingagents.dataflows.social_monitor.parser import parse_json_posts
from tradingagents.dataflows.social_monitor.scoring import hotness_score
from tradingagents.dataflows.social_monitor.sources import SourceTarget, platform_symbol, source_url
from tradingagents.dataflows.social_monitor.storage import SocialMonitorStorage
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
def test_validate_ts_code_accepts_a_share_symbols():
    assert validate_ts_code("000001.sz") == "000001.SZ"
    assert validate_ts_code("600000.SH") == "600000.SH"
    assert validate_ts_code("000300.SH") == "000300.SH"


@pytest.mark.unit
def test_validate_ts_code_rejects_us_symbol():
    with pytest.raises(ValueError):
        validate_ts_code("AAPL")


@pytest.mark.unit
def test_tushare_ohlcv_formats_mock_response(monkeypatch, tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)
    monkeypatch.setattr(
        tushare_provider,
        "_call",
        lambda name, **kwargs: pd.DataFrame(
            [{"ts_code": "000001.SZ", "trade_date": "20240102", "open": 10, "close": 10.5, "vol": 1000}]
        ),
    )

    text = tushare_provider.get_a_share_ohlcv("000001.SZ", "2024-01-01", "2024-01-03")

    assert "A-share OHLCV" in text
    assert "000001.SZ" in text
    assert "20240102" in text


@pytest.mark.unit
def test_cninfo_announcements_extracts_mock_webapi(monkeypatch, tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "records": [
                    {
                        "announcementTitle": "2024年年度报告",
                        "announcementTime": "2024-03-30",
                        "adjunctUrl": "finalpage/test.pdf",
                    }
                ]
            }

    monkeypatch.setattr(cninfo_provider.requests, "get", lambda *args, **kwargs: Response())

    text = cninfo_provider.get_cninfo_announcements("000001.SZ", "2024-01-01", "2024-04-01")

    assert "Cninfo announcements" in text
    assert "2024年年度报告" in text
    assert "static.cninfo.com.cn" in text


@pytest.mark.unit
def test_tushare_announcements_falls_back_to_cninfo(monkeypatch, tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)

    def fake_call(name, **kwargs):
        if name == "anns_d":
            raise tushare_provider.TushareProviderError("permission denied")
        raise AssertionError(f"unexpected Tushare call: {name}")

    monkeypatch.setattr(tushare_provider, "_call", fake_call)
    monkeypatch.setattr(
        cninfo_provider,
        "get_cninfo_announcements",
        lambda *args, **kwargs: "## Cninfo announcements\n\n| title |\n|---|\n| 年度报告 |",
    )

    text = tushare_provider.get_announcements("000001.SZ", "2024-01-01", "2024-04-01")

    assert "Announcement source fallback" in text
    assert "permission denied" in text
    assert "年度报告" in text


@pytest.mark.unit
def test_news_mcp_falls_back_to_jin10(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENNEWS_MCP_URL", "https://opennews.example/mcp")

    def fake_call(url, token, tool_name, args):
        calls.append((url, tool_name, args))
        if len(calls) == 1:
            raise RuntimeError("opennews down")
        return [{"title": "A股政策更新", "source": "jin10", "time": "2024-01-01"}]

    monkeypatch.setattr(mcp_news_provider, "_call_mcp_sync", fake_call)

    text = mcp_news_provider.search_a_share_news("A股", "2024-01-01", "2024-01-02")

    assert "A股政策更新" in text
    assert calls[0][1] == "search_news"
    assert calls[1][1] == "search_news"
    assert calls[1][2] == {"keyword": "A股"}


@pytest.mark.unit
def test_news_opennews_rest_primary(monkeypatch):
    monkeypatch.delenv("OPENNEWS_MCP_URL", raising=False)
    monkeypatch.setattr(
        mcp_news_provider,
        "_call_opennews_rest",
        lambda query, limit=20: [
            {
                "title": "宁德时代发布储能新品",
                "newsType": "jin10",
                "createdAt": "2026-04-28T09:30:00",
                "summaryZh": "储能业务扩张。",
                "url": "https://example.com/news",
                "aiRating": {"score": 82, "signal": "long"},
            }
        ],
    )

    text = mcp_news_provider.search_a_share_news("宁德时代", "2026-04-28", "2026-04-28")

    assert "宁德时代发布储能新品" in text
    assert "long" in text
    assert "82" in text


@pytest.mark.unit
def test_news_opennews_rest_handles_ts_text_and_html(monkeypatch):
    monkeypatch.delenv("OPENNEWS_MCP_URL", raising=False)
    monkeypatch.setattr(
        mcp_news_provider,
        "_call_opennews_rest",
        lambda query, limit=20: [
            {
                "newsType": "jin10",
                "text": '<b>重要新闻</b><br/><span class="section-news">英维克午后翻红</span>',
                "ts": "2026-04-22T06:12:12Z",
                "aiRating": {"score": 15, "signal": "neutral"},
            }
        ],
    )

    text = mcp_news_provider.search_a_share_news("英维克", "2026-04-22", "2026-04-22")

    assert "2026-04-22T06:12:12Z" in text
    assert "重要新闻 英维克午后翻红" in text
    assert "<span" not in text


@pytest.mark.unit
def test_news_search_retries_simplified_company_query(monkeypatch):
    calls = []
    monkeypatch.delenv("OPENNEWS_MCP_URL", raising=False)

    def fake_opennews(query, limit=20):
        calls.append(query)
        if query == "英维克":
            return [{"text": "英维克一季度业绩说明会回应液冷订单", "ts": "2026-04-23T09:00:00Z"}]
        return []

    monkeypatch.setattr(mcp_news_provider, "_call_opennews_rest", fake_opennews)
    monkeypatch.setattr(mcp_news_provider, "_call_mcp_sync", lambda *args, **kwargs: [])

    text = mcp_news_provider.search_a_share_news("002837.SZ 英维克", "2026-04-01", "2026-04-29")

    assert calls[:2] == ["002837.SZ 英维克", "英维克"]
    assert "业绩说明会回应液冷订单" in text


@pytest.mark.unit
def test_news_search_retries_first_company_token(monkeypatch):
    calls = []
    monkeypatch.delenv("OPENNEWS_MCP_URL", raising=False)

    def fake_opennews(query, limit=20):
        calls.append(query)
        if query == "英维克":
            return [{"text": "英维克液冷业务新闻", "ts": "2026-04-14T10:00:00Z"}]
        return []

    monkeypatch.setattr(mcp_news_provider, "_call_opennews_rest", fake_opennews)
    monkeypatch.setattr(mcp_news_provider, "_call_mcp_sync", lambda *args, **kwargs: [])

    text = mcp_news_provider.search_a_share_news("英维克 温控 液冷 数据中心", "2026-04-01", "2026-04-29")

    assert calls[:2] == ["英维克 温控 液冷 数据中心", "英维克"]
    assert "英维克液冷业务新闻" in text


@pytest.mark.unit
def test_news_mcp_flatten_reads_nested_items():
    rows = mcp_news_provider._flatten_mcp_result(
        {
            "data": {
                "has_more": False,
                "items": [{"title": "嵌套新闻", "time": "2026-04-29"}],
            },
            "status": 200,
        }
    )

    assert rows == [{"title": "嵌套新闻", "time": "2026-04-29"}]


@pytest.mark.unit
def test_realtime_news_disabled_by_default(tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    config["realtime_news_enabled"] = False
    set_config(config)

    text = mcp_news_provider.get_a_share_realtime_news("000001.SZ")

    assert "disabled by configuration" in text


@pytest.mark.unit
def test_social_cache_reports_coverage_when_empty(tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)

    text = social_provider.get_a_share_social_sentiment("000001.SZ", "2024-01-01", "2024-01-31")

    assert "No captured Eastmoney Guba" in text
    assert "authorized JSONL imports" in text


@pytest.mark.unit
def test_social_cache_reads_authorized_import(tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)
    path = social_provider.social_cache_path()
    path.write_text(
        json.dumps(
            {
                "source": "authorized_import",
                "time": "2024-01-10",
                "title": "000001.SZ 讨论热度上升",
                "content": "资金关注",
                "confidence": "high",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    text = social_provider.get_a_share_social_sentiment("000001.SZ", "2024-01-01", "2024-01-31")

    assert "讨论热度上升" in text
    assert "high" in text


@pytest.mark.unit
def test_social_monitor_source_symbol_mapping():
    assert platform_symbol("300750.SZ", "eastmoney_guba") == "300750"
    assert source_url("300750.SZ", "eastmoney_guba") == "https://guba.eastmoney.com/list,300750.html"
    with pytest.raises(ValueError):
        platform_symbol("300750.SZ", "unsupported_source")


@pytest.mark.unit
def test_social_monitor_parser_extracts_common_json_shapes():
    payload = {
        "data": {
            "items": [
                {
                    "id": "p1",
                    "title": "宁德时代利好突破",
                    "content": "继续看多",
                    "user": {"id": "u1", "screen_name": "tester"},
                    "createdAt": "2026-04-28T09:30:00",
                    "commentCount": 5,
                    "likeCount": 8,
                    "viewCount": 1000,
                }
            ]
        }
    }

    posts = parse_json_posts(payload, "eastmoney_guba", "300750.SZ", "300750")

    assert len(posts) == 1
    assert posts[0]["post_id"] == "p1"
    assert posts[0]["author"] == "tester"
    assert posts[0]["sentiment"] == "positive"


@pytest.mark.unit
def test_social_monitor_storage_deduplicates_and_social_provider_reads(tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)
    storage = SocialMonitorStorage()
    post = {
        "source": "eastmoney_guba",
        "ts_code": "300750.SZ",
        "platform_symbol": "300750",
        "post_id": "p1",
        "title": "宁德时代利好突破",
        "content": "继续看多",
        "author": "tester",
        "author_id": "u1",
        "created_at": "2026-04-28T09:30:00",
        "captured_at": "2026-04-28T09:31:00",
        "reply_count": 5,
        "like_count": 8,
        "read_count": 1000,
        "repost_count": 0,
        "url": "https://guba.eastmoney.com.cn/news,300750,p1.html",
        "text_signature": "sig1",
        "sentiment": "positive",
        "sentiment_score": 0.4,
        "hotness_score": 20,
        "confidence": "high",
        "raw_json": "{}",
    }

    assert storage.insert_posts([post, post]) == 1
    text = social_provider.get_a_share_social_sentiment("300750.SZ", "2026-04-28", "2026-04-28")

    assert "Forum social sentiment" in text
    assert "宁德时代利好突破" in text
    assert "avg_sentiment_score" in text


@pytest.mark.unit
def test_social_monitor_hotness_time_decay():
    fresh = hotness_score(1000, 5, 10, 0, "2026-04-28T09:00:00", "2026-04-28T09:00:00")
    stale = hotness_score(1000, 5, 10, 0, "2026-04-28T08:00:00", "2026-04-28T09:00:00")

    assert stale < fresh
    assert stale == pytest.approx(fresh * 0.5, rel=0.05)


@pytest.mark.unit
def test_social_monitor_coverage_reports_failed_run(tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)
    storage = SocialMonitorStorage()
    run_id = storage.begin_run("eastmoney_guba", "300750.SZ")
    storage.finish_run(run_id, "error", 0, 0, "verification required")

    text = social_provider.get_social_monitoring_coverage("300750.SZ")

    assert "verification required" in text
    assert "eastmoney_guba" in text


@pytest.mark.unit
def test_social_provider_collects_eastmoney_during_analysis_once(monkeypatch, tmp_path):
    config = DEFAULT_CONFIG.copy()
    config["data_cache_dir"] = str(tmp_path)
    set_config(config)
    social_provider._COLLECTED_DURING_ANALYSIS.clear()
    monkeypatch.setenv("SOCIAL_MONITOR_ENABLED", "true")
    monkeypatch.setenv("SOCIAL_MONITOR_COLLECT_DURING_ANALYSIS", "true")
    monkeypatch.setenv("SOCIAL_MONITOR_SOURCES", "eastmoney_guba")
    calls = []

    def fake_collect_once(
        symbols,
        sources=None,
        scroll_seconds=90,
        max_posts_per_symbol=None,
        headless=True,
        max_pages_per_symbol=None,
    ):
        calls.append((symbols, sources, headless, max_pages_per_symbol))
        return [
            {
                "source": "eastmoney_guba",
                "ts_code": symbols[0],
                "status": "success",
                "posts_seen": 3,
                "posts_inserted": 3,
                "error": "",
            }
        ]

    from tradingagents.dataflows.social_monitor import runner

    monkeypatch.setattr(runner, "collect_once", fake_collect_once)

    first = social_provider.get_social_monitoring_coverage("300750.SZ")
    second = social_provider.get_a_share_social_sentiment("300750.SZ", "2026-04-28", "2026-04-29")

    assert len(calls) == 1
    assert calls[0] == (["300750.SZ"], ["eastmoney_guba"], True, 3)
    assert "Eastmoney Guba collection during Social Analyst run" in first
    assert "Eastmoney Guba collection during Social Analyst run" not in second


class _FakePaginationLocator:
    def __init__(self, page, label):
        self.page = page
        self.label = label

    def first(self):
        return self

    async def click(self, timeout=0):
        self.page.clicks.append(self.label)
        if self.label in self.page.failed_clicks:
            raise RuntimeError("click failed")
        self.page.current_page = int(self.label)


class _FakeSocialPage:
    mouse = SimpleNamespace(wheel=lambda *args, **kwargs: None)

    def __init__(self, failed_clicks=None):
        self.gotos = []
        self.clicks = []
        self.current_page = 1
        self.failed_clicks = set(failed_clicks or [])

    def on(self, *_args, **_kwargs):
        return None

    def get_by_text(self, label, exact=True):
        return _FakePaginationLocator(self, label)

    async def goto(self, url, *_args, **_kwargs):
        self.gotos.append(url)
        match = browser_collector.re.search(r"_(\d+)\.html$", url)
        self.current_page = int(match.group(1)) if match else 1

    async def content(self):
        return f"<html>page-{self.current_page}</html>"

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None


def _post_for_page(page_number):
    return {
        "source": "eastmoney_guba",
        "ts_code": "000001.SZ",
        "post_id": f"p{page_number}",
        "title": f"page {page_number}",
    }


@pytest.mark.unit
def test_eastmoney_collector_clicks_pagination_buttons(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="000001.SZ",
        platform_symbol="000001",
        url="https://guba.eastmoney.com/list,000001.html",
    )
    page = _FakeSocialPage()
    scrolls = []
    monkeypatch.delenv("SOCIAL_EASTMONEY_ENABLE_SCROLL", raising=False)

    async def fake_scroll(*_args, **_kwargs):
        scrolls.append("scroll")

    async def no_verification(*_args, **_kwargs):
        return None

    def fake_parse(html, *_args):
        page_number = int(html.split("page-")[1].split("<")[0])
        return [_post_for_page(page_number)]

    monkeypatch.setattr(browser_collector, "_scroll", fake_scroll)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_verification)
    monkeypatch.setattr(browser_collector, "parse_html_posts", fake_parse)

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=10, max_pages=3))

    assert [post["post_id"] for post in posts] == ["p1", "p2", "p3"]
    assert page.gotos == ["https://guba.eastmoney.com/list,000001.html"]
    assert page.clicks == ["2", "3"]
    assert scrolls == []


@pytest.mark.unit
def test_eastmoney_collector_falls_back_to_page_url_when_click_fails(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="000001.SZ",
        platform_symbol="000001",
        url="https://guba.eastmoney.com/list,000001.html",
    )
    page = _FakeSocialPage(failed_clicks={"2"})
    monkeypatch.delenv("SOCIAL_EASTMONEY_ENABLE_SCROLL", raising=False)

    async def no_op(*_args, **_kwargs):
        return None

    def fake_parse(html, *_args):
        page_number = int(html.split("page-")[1].split("<")[0])
        return [_post_for_page(page_number)]

    monkeypatch.setattr(browser_collector, "_scroll", no_op)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_op)
    monkeypatch.setattr(browser_collector, "parse_html_posts", fake_parse)

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=10, max_pages=2))

    assert [post["post_id"] for post in posts] == ["p1", "p2"]
    assert page.gotos == [
        "https://guba.eastmoney.com/list,000001.html",
        "https://guba.eastmoney.com/list,000001_2.html",
    ]
    assert page.clicks == ["2"]


@pytest.mark.unit
def test_eastmoney_collector_stops_at_max_posts(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="000001.SZ",
        platform_symbol="000001",
        url="https://guba.eastmoney.com/list,000001.html",
    )
    page = _FakeSocialPage()
    monkeypatch.delenv("SOCIAL_EASTMONEY_ENABLE_SCROLL", raising=False)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser_collector, "_scroll", no_op)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_op)
    monkeypatch.setattr(browser_collector, "parse_html_posts", lambda *_args: [_post_for_page(1)])

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=1, max_pages=3))

    assert [post["post_id"] for post in posts] == ["p1"]
    assert page.clicks == []


@pytest.mark.unit
def test_eastmoney_collector_respects_max_pages_one(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="000001.SZ",
        platform_symbol="000001",
        url="https://guba.eastmoney.com/list,000001.html",
    )
    page = _FakeSocialPage()
    monkeypatch.delenv("SOCIAL_EASTMONEY_ENABLE_SCROLL", raising=False)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser_collector, "_scroll", no_op)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_op)
    monkeypatch.setattr(browser_collector, "parse_html_posts", lambda *_args: [_post_for_page(1)])

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=10, max_pages=1))

    assert [post["post_id"] for post in posts] == ["p1"]
    assert page.clicks == []


@pytest.mark.unit
def test_eastmoney_collector_scroll_is_opt_in(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="000001.SZ",
        platform_symbol="000001",
        url="https://guba.eastmoney.com/list,000001.html",
    )
    page = _FakeSocialPage()
    scrolls = []
    monkeypatch.setenv("SOCIAL_EASTMONEY_ENABLE_SCROLL", "1")

    async def fake_scroll(*_args, **_kwargs):
        scrolls.append("scroll")

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser_collector, "_scroll", fake_scroll)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_op)
    monkeypatch.setattr(browser_collector, "parse_html_posts", lambda *_args: [_post_for_page(1)])

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=10, max_pages=2))

    assert [post["post_id"] for post in posts] == ["p1"]
    assert scrolls == ["scroll", "scroll"]


@pytest.mark.unit
def test_binance_square_keeps_scroll_strategy(monkeypatch):
    target = SourceTarget(
        source="binance_square",
        ts_code="BTC-USDT-SWAP",
        platform_symbol="BTC",
        url="https://www.binance.com/zh-CN/square",
    )
    page = _FakeSocialPage()
    scrolls = []

    async def fake_scroll(*_args, **_kwargs):
        scrolls.append("scroll")

    async def no_verification(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser_collector, "_scroll", fake_scroll)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_verification)

    posts = asyncio.run(browser_collector._collect_from_page(page, target, scroll_seconds=0, max_posts=10, max_pages=3))

    assert posts == []
    assert page.gotos == ["https://www.binance.com/zh-CN/square"]
    assert page.clicks == []
    assert scrolls == ["scroll"]


@pytest.mark.unit
def test_social_monitor_uses_cdp_browser_when_configured(monkeypatch):
    target = SourceTarget(
        source="eastmoney_guba",
        ts_code="300750.SZ",
        platform_symbol="300750",
        url="https://guba.eastmoney.com/list,300750.html",
    )
    calls = {"cdp": [], "persistent": 0, "closed_page": 0}

    class FakePage:
        mouse = SimpleNamespace(wheel=lambda *args, **kwargs: None)

        def on(self, *_args, **_kwargs):
            return None

        async def goto(self, *_args, **_kwargs):
            return None

        async def content(self):
            return "<html></html>"

        async def close(self):
            calls["closed_page"] += 1

    class FakeContext:
        async def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

        async def new_context(self):
            return FakeContext()

    class FakeChromium:
        async def connect_over_cdp(self, cdp_url):
            calls["cdp"].append(cdp_url)
            return FakeBrowser()

        async def launch_persistent_context(self, *_args, **_kwargs):
            calls["persistent"] += 1
            return FakeContext()

    class FakePlaywrightManager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=FakeChromium())

        async def __aexit__(self, *_args):
            return None

    fake_module = SimpleNamespace(async_playwright=lambda: FakePlaywrightManager())
    monkeypatch.setenv("SOCIAL_BROWSER_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(browser_collector, "_import_playwright", lambda: fake_module)

    async def no_scroll(*_args, **_kwargs):
        return None

    async def no_verification(*_args, **_kwargs):
        return None

    monkeypatch.setattr(browser_collector, "_scroll", no_scroll)
    monkeypatch.setattr(browser_collector, "_raise_if_verification_page", no_verification)

    posts = asyncio.run(browser_collector.collect_target_async(target, scroll_seconds=0, max_posts=5))

    assert posts == []
    assert calls["cdp"] == ["http://127.0.0.1:9222"]
    assert calls["persistent"] == 0
    assert calls["closed_page"] == 1


@pytest.mark.unit
def test_ifind_real_time_quote_formats_mock_tables(monkeypatch):
    monkeypatch.setenv("IFIND_ENABLED", "true")
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "dummy")
    monkeypatch.setattr(
        ifind_provider,
        "_post",
        lambda endpoint, payload: (
            {
                "tables": [
                    {
                        "thscode": "300750.SZ",
                        "table": {
                            "time": ["2026-04-28 10:00:00"],
                            "latest": [427.5],
                            "amount": [123456789],
                        },
                    }
                ]
            },
            None,
        ),
    )

    text = ifind_provider.real_time_quote("300750.SZ")

    assert "iFinD real-time quote" in text
    assert "300750.SZ" in text
    assert "427.5" in text


@pytest.mark.unit
def test_ifind_error_markdown_includes_response_code(monkeypatch):
    monkeypatch.setenv("IFIND_ENABLED", "true")
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "dummy")
    monkeypatch.setattr(
        ifind_provider,
        "_post",
        lambda endpoint, payload: (
            None,
            ifind_provider.IFindError(endpoint, "permission denied", http_status=403, error_code="403001"),
        ),
    )

    text = ifind_provider.real_time_quote("300750.SZ")

    assert "iFinD real-time quote unavailable" in text
    assert "403" in text
    assert "403001" in text
    assert "permission denied" in text


@pytest.mark.unit
def test_market_snapshot_appends_ifind_optional_section(monkeypatch):
    monkeypatch.setenv("IFIND_ENABLED", "true")
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "dummy")
    monkeypatch.setattr(
        tushare_provider,
        "_call",
        lambda name, **kwargs: pd.DataFrame(
            [{"ts_code": "300750.SZ", "trade_date": "20260428", "close": 427.5}]
        ),
    )
    monkeypatch.setattr(tushare_provider, "_safe_call", lambda name, **kwargs: None)
    monkeypatch.setattr(ifind_provider, "real_time_quote", lambda ts_code: "## iFinD real-time quote\n\nok")

    text = tushare_provider.get_a_share_market_snapshot("300750.SZ", "2026-04-28")

    assert "Daily bar for 300750.SZ" in text
    assert "iFinD real-time quote" in text


@pytest.mark.unit
def test_social_hotness_appends_ifind_optional_section(monkeypatch):
    monkeypatch.setenv("IFIND_ENABLED", "true")
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "dummy")
    monkeypatch.setattr(social_provider, "_safe_call", lambda name, **kwargs: pd.DataFrame())
    monkeypatch.setattr(ifind_provider, "popularity_signal", lambda ts_code, trade_date=None: "## iFinD smart stock picking\n\nok")

    text = social_provider.get_a_share_hotness("300750.SZ", "2026-04-28")

    assert "iFinD smart stock picking" in text


@pytest.mark.unit
def test_graph_tool_nodes_only_expose_a_share_tools():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = DEFAULT_CONFIG.copy()
    nodes = TradingAgentsGraph._create_tool_nodes(graph)

    names = {
        key: {tool.name for tool in node.tools_by_name.values()}
        for key, node in nodes.items()
    }

    assert names["market"] == {
        "get_a_share_ohlcv",
        "get_a_share_market_snapshot",
        "get_a_share_indicators",
        "get_a_share_moneyflow",
    }
    assert "get_news" not in names["news"]
    assert "get_insider_transactions" not in names["news"]
    assert "get_fundamentals" not in names["fundamentals"]
    assert "get_cninfo_announcements" not in names["fundamentals"]
    assert "get_a_share_announcements" in names["fundamentals"]
    assert "get_a_share_announcements" in names["news"]
    assert "get_a_share_realtime_news" not in names["news"]


@pytest.mark.unit
def test_graph_tool_nodes_include_realtime_when_enabled():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = DEFAULT_CONFIG.copy()
    graph.config["realtime_news_enabled"] = True
    nodes = TradingAgentsGraph._create_tool_nodes(graph)

    names = {tool.name for tool in nodes["news"].tools_by_name.values()}

    assert "get_a_share_realtime_news" in names
