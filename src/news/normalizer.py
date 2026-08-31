from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

NEWS_CUTOFF_RULES_VERSION = "1.0"


@dataclass(frozen=True)
class NewsCutoffResult:
    """新闻在指定信息截面上的可用性筛选结果。"""

    eligible: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    report: dict[str, Any]


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
                "published_at_utc": _isoformat_utc(_parse_news_datetime(result.get("published_date"), timezone.utc)),
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


def filter_news_by_cutoff(
    items: Iterable[dict[str, Any]],
    *,
    cutoff: date | datetime,
    timezone_name: str = "Asia/Shanghai",
) -> NewsCutoffResult:
    """按公开发布时间筛选新闻，防止将截面之后的内容带入预测。

    ``date`` 截面按交易所本地时间当天 23:59:59.999999 处理；无时区
    时间戳按 ``timezone_name`` 解释。没有可解析发布时间的新闻会保留在
    审计结果中，但不会进入评分。
    """
    tz = _resolve_timezone(timezone_name)
    cutoff_dt = _coerce_cutoff(cutoff, tz)
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    counts = {"eligible": 0, "missing_published_at": 0, "invalid_published_at": 0, "after_cutoff": 0}

    for item in items:
        candidate = dict(item)
        published = _parse_news_datetime(candidate.get("published_at"), tz)
        if not candidate.get("published_at"):
            reason = "缺少 published_at，无法确认信息截面"
            status = "excluded_missing_published_at"
            counts["missing_published_at"] += 1
        elif published is None:
            reason = "published_at 无法解析"
            status = "excluded_invalid_published_at"
            counts["invalid_published_at"] += 1
        elif published > cutoff_dt:
            reason = f"published_at 晚于信息截面 {cutoff_dt.isoformat()}"
            status = "excluded_after_cutoff"
            counts["after_cutoff"] += 1
        else:
            candidate["published_at_utc"] = published.astimezone(timezone.utc).isoformat()
            candidate["cutoff_status"] = "eligible"
            candidate["data_status"] = "eligible"
            eligible.append(candidate)
            counts["eligible"] += 1
            continue
        candidate["cutoff_status"] = status
        candidate["data_status"] = "excluded"
        candidate["exclusion_reason"] = reason
        if published is not None:
            candidate["published_at_utc"] = published.astimezone(timezone.utc).isoformat()
        excluded.append(candidate)

    warnings: list[str] = []
    if counts["missing_published_at"]:
        warnings.append(f"{counts['missing_published_at']} 条新闻缺少发布时间，未进入消息面评分")
    if counts["invalid_published_at"]:
        warnings.append(f"{counts['invalid_published_at']} 条新闻发布时间无法解析，未进入消息面评分")
    if counts["after_cutoff"]:
        warnings.append(f"{counts['after_cutoff']} 条新闻晚于信息截面，已排除以防止未来函数")
    return NewsCutoffResult(
        eligible=eligible,
        excluded=excluded,
        report={
            "status": "validated_with_warning" if warnings else "validated",
            "rules_version": NEWS_CUTOFF_RULES_VERSION,
            "timezone": timezone_name,
            "cutoff": cutoff_dt.isoformat(),
            "counts": counts,
            "warnings": warnings,
        },
    )


def _coerce_cutoff(value: date | datetime, tz: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, time.max, tzinfo=tz)
    raise TypeError("cutoff 必须是 date 或 datetime")


def _resolve_timezone(timezone_name: str) -> Any:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"不支持的新闻时区: {timezone_name}") from exc


def _parse_news_datetime(value: Any, default_tz: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def _isoformat_utc(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


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
