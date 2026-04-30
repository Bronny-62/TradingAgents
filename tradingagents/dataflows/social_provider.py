"""Compliance-first social sentiment cache for A-share monitoring."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .a_share_utils import cache_dir, dataframe_preview, parse_date, validate_ts_code
from .mcp_news_provider import read_news_events
from .social_monitor.storage import get_social_monitor_summary, query_social_posts
from .tushare_provider import _safe_call

_COLLECTED_DURING_ANALYSIS: set[str] = set()


def social_cache_path() -> Path:
    return cache_dir("social_cache") / "events.jsonl"


def append_social_event(event: dict[str, Any]) -> None:
    path = social_cache_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def read_social_events() -> list[dict[str, Any]]:
    path = social_cache_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def get_a_share_social_sentiment(ts_code: str, start_date: str, end_date: str) -> str:
    ts_code = validate_ts_code(ts_code)
    collection_note = _maybe_collect_eastmoney_during_analysis(ts_code)
    rows = []
    code = ts_code.split(".")[0]
    start = parse_date(start_date)
    end = parse_date(end_date)

    forum_rows = query_social_posts(ts_code, start_date, end_date, limit=50)
    if forum_rows:
        sections = [
            _format_forum_summary(ts_code, forum_rows),
            "### Top forum posts\n\n"
            + dataframe_preview(pd.DataFrame([_forum_row_for_display(row) for row in forum_rows[:20]]), max_rows=20),
        ]
        if collection_note:
            sections.append(collection_note)
        return "\n\n".join(sections)

    for item in read_social_events():
        text = " ".join(str(item.get(k, "")) for k in ("title", "content", "symbols", "source"))
        date = _date_from_item(item)
        if date and not (start <= date <= end):
            continue
        if ts_code in text or code in text:
            rows.append(_normalize(item, "authorized_import"))

    for item in read_news_events():
        text = " ".join(str(item.get(k, "")) for k in ("title", "content", "symbols"))
        date = _date_from_item(item)
        if date and not (start <= date <= end):
            continue
        if ts_code in text or code in text:
            event = _normalize(item, "news_proxy")
            event["confidence"] = "low"
            rows.append(event)

    if not rows:
        text = (
            f"## Social sentiment coverage for {ts_code}\n\n"
            "No captured Eastmoney Guba browser-session forum posts are available for this date range. "
            "Enable the Step 2 Eastmoney Guba authorization flow or run `tradingagents social-login` once, then "
            f"`tradingagents social-monitor --symbols {ts_code} --once --sources eastmoney_guba` to populate "
            "authorized local forum data. Current fallback coverage: "
            "authorized JSONL imports, Tushare hotness where available, and news-derived proxy events."
        )
        return f"{text}\n\n{collection_note}" if collection_note else text
    df = pd.DataFrame(rows)
    text = f"## Social sentiment proxy for {ts_code}\n\n{dataframe_preview(df, max_rows=20)}"
    return f"{text}\n\n{collection_note}" if collection_note else text


def get_a_share_hotness(ts_code: str, trade_date: str) -> str:
    from . import ifind_provider

    ts_code = validate_ts_code(ts_code)
    sections = []
    for api_name in ("dc_hot", "ths_hot"):
        df = _safe_call(api_name, ts_code=ts_code, trade_date=trade_date.replace("-", ""))
        if df is not None and not df.empty:
            sections.append(f"### {api_name}\n\n{dataframe_preview(df, max_rows=20)}")
    ifind_section = ifind_provider.optional_section(
        "iFinD social hotness unavailable",
        ifind_provider.popularity_signal,
        ts_code,
        trade_date,
    )
    if ifind_section:
        sections.append(ifind_section)
    if not sections:
        return (
            f"No hotness rows available for {ts_code} on {trade_date}. "
            "This may be caused by Tushare permissions, iFinD token/configuration, endpoint availability, "
            "or no ranking entry."
        )
    return "\n\n".join(sections)


def get_social_monitoring_coverage(ts_code: str) -> str:
    from . import ifind_provider

    ts_code = validate_ts_code(ts_code)
    collection_note = _maybe_collect_eastmoney_during_analysis(ts_code)
    summaries = get_social_monitor_summary(ts_code)
    monitor_enabled = os.getenv("SOCIAL_MONITOR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    rows = []
    if summaries:
        for item in summaries:
            rows.append(
                {
                    "source": item.get("source", "unknown"),
                    "status": item.get("status") or "captured",
                    "method": "authorized Playwright browser session",
                    "posts": item.get("post_count") or item.get("posts_inserted") or 0,
                    "last_seen": item.get("last_captured_at") or item.get("finished_at") or item.get("started_at") or "",
                    "confidence": _confidence_from_summary(item),
                    "error": item.get("error", ""),
                }
            )
    rows.extend([
        {
            "source": "Eastmoney Guba",
            "status": "browser monitor configured" if monitor_enabled else "browser monitor disabled",
            "method": "Playwright local profile; no anti-bot bypass",
            "posts": "",
            "last_seen": "",
            "confidence": "depends on latest successful capture",
            "error": "",
        },
        {
            "source": "Tushare hotness",
            "status": "best-effort",
            "method": "dc_hot / ths_hot if permissions allow",
            "posts": "",
            "last_seen": "",
            "confidence": "medium when available",
            "error": "",
        },
        {
            "source": "iFinD smart stock picking",
            "status": "configured"
            if ifind_provider.is_enabled() and ifind_provider.has_credentials()
            else "disabled or token missing",
            "method": "QuantAPI smart_stock_picking for Tonghuashun popularity-style signals",
            "posts": "",
            "last_seen": "",
            "confidence": "medium when available; diagnostic rows report http_status/error_code on failure",
            "error": "",
        },
        {
            "source": "News proxy",
            "status": "available from local news cache",
            "method": "opennews/Jin10 events mentioning the stock",
            "posts": "",
            "last_seen": "",
            "confidence": "low to medium",
            "error": "",
        },
    ])
    text = f"## Social monitoring coverage for {ts_code}\n\n{dataframe_preview(pd.DataFrame(rows), max_rows=10)}"
    return f"{text}\n\n{collection_note}" if collection_note else text


def _maybe_collect_eastmoney_during_analysis(ts_code: str) -> str:
    ts_code = validate_ts_code(ts_code)
    enabled = os.getenv("SOCIAL_MONITOR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    collect_enabled = os.getenv("SOCIAL_MONITOR_COLLECT_DURING_ANALYSIS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    sources = {item.strip().lower() for item in os.getenv("SOCIAL_MONITOR_SOURCES", "").split(",") if item.strip()}
    if not enabled or not collect_enabled or "eastmoney_guba" not in sources:
        return ""
    if ts_code in _COLLECTED_DURING_ANALYSIS:
        return ""

    _COLLECTED_DURING_ANALYSIS.add(ts_code)
    try:
        from .social_monitor.runner import collect_once

        rows = collect_once(
            [ts_code],
            sources=["eastmoney_guba"],
            scroll_seconds=int(os.getenv("SOCIAL_ANALYZE_GUBA_SCROLL_SECONDS", "45")),
            max_posts_per_symbol=int(os.getenv("SOCIAL_MONITOR_MAX_POSTS_PER_SYMBOL", "200") or 200),
            headless=True,
            max_pages_per_symbol=int(
                os.getenv(
                    "SOCIAL_ANALYZE_GUBA_MAX_PAGES",
                    os.getenv("SOCIAL_MONITOR_MAX_PAGES", "3"),
                )
                or 3
            ),
        )
    except Exception as exc:
        rows = [{"source": "eastmoney_guba", "ts_code": ts_code, "status": "error", "posts_seen": 0, "posts_inserted": 0, "error": str(exc)}]

    return "## Eastmoney Guba collection during Social Analyst run\n\n" + dataframe_preview(
        pd.DataFrame(rows),
        max_rows=5,
    )


def _date_from_item(item: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "received_at", "time", "date"):
        value = item.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                return parse_date(str(value)[:10])
            except ValueError:
                continue
    return None


def _normalize(item: dict[str, Any], method: str) -> dict[str, Any]:
    return {
        "source": item.get("source") or "unknown",
        "time": item.get("published_at") or item.get("received_at") or item.get("time") or "",
        "title": item.get("title") or "",
        "snippet": item.get("content") or item.get("summary") or "",
        "hotness": item.get("hotness") or item.get("score") or "",
        "sentiment": item.get("sentiment") or item.get("signal") or "unclassified",
        "confidence": item.get("confidence") or "medium",
        "url": item.get("url") or "",
        "method": item.get("method") or method,
    }


def _format_forum_summary(ts_code: str, rows: list[dict[str, Any]]) -> str:
    df = pd.DataFrame(rows)
    total = len(df)
    author_count = df[["author_id", "author"]].fillna("").agg("|".join, axis=1).nunique()
    avg_sentiment = pd.to_numeric(df["sentiment_score"], errors="coerce").fillna(0).mean()
    sentiment_counts = df["sentiment"].fillna("unclassified").value_counts().to_dict()
    last_seen = df["captured_at"].max()
    source_counts = df["source"].fillna("unknown").value_counts().to_dict()
    summary_rows = [
        {"metric": "forum_posts", "value": total},
        {"metric": "unique_authors", "value": author_count},
        {"metric": "avg_sentiment_score", "value": round(float(avg_sentiment), 4)},
        {"metric": "positive_count", "value": sentiment_counts.get("positive", 0)},
        {"metric": "negative_count", "value": sentiment_counts.get("negative", 0)},
        {"metric": "neutral_count", "value": sentiment_counts.get("neutral", 0)},
        {"metric": "uncertain_count", "value": sentiment_counts.get("uncertain", 0)},
        {"metric": "sources", "value": json.dumps(source_counts, ensure_ascii=False)},
        {"metric": "last_captured_at", "value": last_seen},
    ]
    return f"## Forum social sentiment for {ts_code}\n\n{dataframe_preview(pd.DataFrame(summary_rows), max_rows=20)}"


def _forum_row_for_display(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": row.get("source"),
        "time": row.get("created_at") or row.get("captured_at"),
        "title": row.get("title"),
        "sentiment": row.get("sentiment"),
        "sentiment_score": row.get("sentiment_score"),
        "hotness_score": row.get("hotness_score"),
        "reply_count": row.get("reply_count"),
        "like_count": row.get("like_count"),
        "read_count": row.get("read_count"),
        "confidence": row.get("confidence"),
        "url": row.get("url"),
    }


def _confidence_from_summary(item: dict[str, Any]) -> str:
    if item.get("error"):
        return "none until capture succeeds"
    post_count = int(item.get("post_count") or item.get("posts_inserted") or 0)
    if post_count >= 30:
        return "high"
    if post_count > 0:
        return "medium"
    return "none"
