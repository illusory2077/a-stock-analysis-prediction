from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}


def normalize_tavily_response(
    response: dict[str, Any],
    *,
    query: str,
    retrieved_at: datetime | None = None,
    related_symbols: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """将 Tavily 响应转换为项目统一新闻字段。"""
    retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    symbols = [symbol.strip() for symbol in (related_symbols or []) if symbol.strip()]
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(response.get("results") or []):
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        title = _clean_text(result.get("title"))
        content = _clean_text(result.get("raw_content") or result.get("content"))
        if not title and not url:
            continue
        canonical_url = canonicalize_url(url)
        rows.append(
            {
                "news_id": news_id(title=title, url=canonical_url),
                "query": query,
                "title": title,
                "url": url,
                "canonical_url": canonical_url,
                "source": _source_from_url(url),
                "published_at": result.get("published_date") or None,
                "retrieved_at": retrieved,
                "content": content,
                "summary": _clean_text(result.get("content")),
                "score": _as_float(result.get("score")),
                "related_symbols": symbols,
                "source_provider": "tavily",
                "source_record_id": str(result.get("id") or index),
                "data_status": "normalized",
            }
        )
    return rows


def deduplicate_news(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按规范化 URL 和标题指纹去重，保留首次出现的结果。"""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = _clean_text(item.get("title"))
        canonical_url = canonicalize_url(str(item.get("canonical_url") or item.get("url") or ""))
        key = canonical_url or _normalize_title(title)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    filtered = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in _TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(filtered), ""))


def news_id(*, title: str, url: str) -> str:
    basis = canonicalize_url(url) or _normalize_title(title)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _source_from_url(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
