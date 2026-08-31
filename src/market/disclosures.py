"""公告与财报的统一标准化、质量检查和公开披露时间截面控制。"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

DISCLOSURE_RULES_VERSION = "1.0"
DISCLOSURE_COLUMNS = [
    "symbol", "exchange", "asset_type", "report_type", "title", "announcement_type",
    "report_period", "published_at", "published_at_utc", "trade_date", "content_summary",
    "source", "source_url", "source_record_id", "retrieved_at", "data_version", "data_status",
]

_ALIASES = {
    "symbol": ("symbol", "ts_code", "代码", "股票代码", "证券代码"),
    "title": ("title", "file_name", "公告标题", "标题", "文件名", "公告名称"),
    "announcement_type": ("announcement_type", "type", "公告类型", "分类", "报告类型", "report_type"),
    "report_period": ("report_period", "end_date", "报告期", "报告期末", "截止日期"),
    "published_at": ("published_at", "ann_date", "f_ann_date", "公告日期", "披露日期", "发布时间", "发布日期"),
    "source_url": ("source_url", "url", "公告链接", "链接", "URL"),
    "source_record_id": ("source_record_id", "record_id", "id", "公告ID"),
    "content_summary": ("content_summary", "summary", "content", "摘要", "内容", "说明"),
}


@dataclass(frozen=True)
class DisclosureQualityResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


@dataclass(frozen=True)
class DisclosureCutoffResult:
    eligible: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    report: dict[str, Any]


class DisclosureNormalizationError(ValueError):
    """公告/财报输入无法转换为统一结构。"""


def normalize_disclosures(
    data: Any,
    *,
    symbol: str,
    report_type: str = "announcement",
    source: str = "unknown",
    retrieved_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    data_version: str | None = None,
) -> pd.DataFrame:
    frame = _as_dataframe(data)
    retrieved = _as_utc(retrieved_at or datetime.now(timezone.utc)).isoformat()
    requested_symbol = _canonical_symbol(symbol)
    mapping = _resolve_mapping(frame.columns)
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row_number, (_, raw_row) in enumerate(frame.iterrows(), start=1):
        raw = raw_row.to_dict()
        canonical = {key: raw.get(column) for key, column in mapping.items()}
        published_value = canonical.get("published_at")
        if report_type == "announcement" and _text(raw.get("rec_time")):
            # Tushare anns_d 的 rec_time 是接口返回的实际记录时间，优先于日期字段。
            published_value = raw.get("rec_time")
        elif report_type == "financial_report" and _text(raw.get("f_ann_date")):
            # 财报以实际公告日期为公开披露时间；ann_date 仅作兼容回退。
            published_value = raw.get("f_ann_date")
        if _text(published_value) is None:
            published_value = raw.get("实际公告日期")
        published = _parse_datetime(published_value, ZoneInfo("Asia/Shanghai"))
        report_period = _parse_date(canonical.get("report_period"))
        title = _text(canonical.get("title")) or ""
        summary = _text(canonical.get("content_summary")) or ""
        if report_type == "financial_report" and not title and report_period:
            title = f"{report_period.isoformat()} 财务报告"
        url = _text(canonical.get("source_url")) or ""
        record_id = _text(canonical.get("source_record_id")) or _record_id(
            requested_symbol, report_type, published, title, url, row_number
        )
        row: dict[str, Any] = {
            "symbol": requested_symbol,
            "exchange": _exchange(requested_symbol),
            "asset_type": "stock",
            "report_type": report_type,
            "title": title,
            "announcement_type": _text(canonical.get("announcement_type")),
            "report_period": report_period,
            "published_at": published.isoformat() if published else None,
            "published_at_utc": published.astimezone(timezone.utc).isoformat() if published else None,
            "trade_date": published.date() if published else None,
            "content_summary": summary,
            "source": source,
            "source_url": url,
            "source_record_id": record_id,
            "retrieved_at": retrieved,
            "data_version": data_version,
            "data_status": "normalized",
        }
        errors: list[str] = []
        if not title and not summary and not url:
            errors.append("公告/财报缺少标题、摘要和链接")
        if start_date and row["trade_date"] and row["trade_date"] < start_date:
            errors.append(f"published_at 早于请求范围 {start_date.isoformat()}")
        if end_date and row["trade_date"] and row["trade_date"] > end_date:
            errors.append(f"published_at 晚于请求范围 {end_date.isoformat()}")
        if row["published_at"] is None:
            warnings.append("存在缺少公开披露时间的记录；该记录只能进入审计，不能进入预测")
        if errors:
            row["data_status"] = "rejected"
            row["_rejection_reason"] = "; ".join(errors)
            rejected.append(row)
        else:
            rows.append(row)

    result = pd.DataFrame(rows, columns=DISCLOSURE_COLUMNS)
    rejected_frame = pd.DataFrame(rejected, columns=[*DISCLOSURE_COLUMNS, "_rejection_reason"])
    result.attrs.update(
        input_rows=len(frame), rejected_data=rejected_frame,
        normalization_warnings=list(dict.fromkeys(warnings)), column_mapping=mapping,
    )
    return result


def validate_disclosures(
    data: pd.DataFrame,
    *,
    rejected_data: pd.DataFrame | None = None,
    input_rows: int | None = None,
    warnings: Iterable[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> DisclosureQualityResult:
    if not isinstance(data, pd.DataFrame):
        raise DisclosureNormalizationError("公告/财报标准化结果必须是 DataFrame")
    frame = data.copy()
    rejected_parts = [rejected_data.copy()] if isinstance(rejected_data, pd.DataFrame) and not rejected_data.empty else []
    errors: list[str] = []
    report_warnings = list(warnings or [])
    missing_columns = [column for column in ("symbol", "report_type", "published_at", "source_record_id") if column not in frame.columns]
    if missing_columns:
        errors.append(f"缺少标准字段: {', '.join(missing_columns)}")
    duplicate_count = 0
    if "source_record_id" in frame.columns:
        duplicate_mask = frame["source_record_id"].astype(str).duplicated(keep="first")
        duplicate_count = int(duplicate_mask.sum())
        if duplicate_count:
            rejected = frame.loc[duplicate_mask].copy()
            rejected["_rejection_reason"] = "重复 source_record_id"
            rejected_parts.append(rejected)
            frame = frame.loc[~duplicate_mask].copy()
    missing_published = int(frame["published_at"].isna().sum()) if "published_at" in frame.columns else len(frame)
    if missing_published:
        report_warnings.append(f"{missing_published} 条记录缺少可解析公开披露时间，已禁止进入预测")
    frame.attrs = {}
    rejected_frame = _concat_rejected(rejected_parts)
    total = int(input_rows if input_rows is not None else len(frame) + len(rejected_frame))
    status = "invalid" if errors else "validated_with_warning" if report_warnings or duplicate_count else "validated"
    report = {
        "disclosure_rules_version": DISCLOSURE_RULES_VERSION,
        "status": status,
        "input_rows": total,
        "output_rows": len(frame),
        "rejected_rows": len(rejected_frame),
        "duplicates_removed": duplicate_count,
        "missing_published_at": missing_published,
        "warnings": list(dict.fromkeys(report_warnings)),
        "errors": list(dict.fromkeys(errors)),
        "column_mapping": column_mapping or {},
    }
    return DisclosureQualityResult(frame, rejected_frame, report)


def filter_disclosures_by_cutoff(
    items: Iterable[dict[str, Any]] | pd.DataFrame,
    *,
    cutoff: date | datetime,
    timezone_name: str = "Asia/Shanghai",
) -> DisclosureCutoffResult:
    """只允许公开披露时间不晚于信息截面的公告/财报进入预测。"""
    tz = _resolve_timezone(timezone_name)
    candidates = items.to_dict(orient="records") if isinstance(items, pd.DataFrame) else [dict(item) for item in items]
    cutoff_dt = _coerce_cutoff(cutoff, tz)
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    counts = {"eligible": 0, "missing_published_at": 0, "invalid_published_at": 0, "after_cutoff": 0}
    for item in candidates:
        candidate = dict(item)
        raw = candidate.get("published_at") or candidate.get("published_at_utc")
        parsed = _parse_datetime(raw, tz)
        if raw in (None, ""):
            reason = "missing_published_at"; counts[reason] += 1
        elif parsed is None:
            reason = "invalid_published_at"; counts[reason] += 1
        elif parsed > cutoff_dt:
            reason = "after_cutoff"; counts[reason] += 1
        else:
            candidate["published_at_utc"] = parsed.astimezone(timezone.utc).isoformat()
            candidate["cutoff_status"] = "eligible"
            eligible.append(candidate); counts["eligible"] += 1
            continue
        candidate["cutoff_status"] = "excluded"
        candidate["cutoff_exclusion_reason"] = reason
        excluded.append(candidate)
    warnings: list[str] = []
    if counts["missing_published_at"] or counts["invalid_published_at"]:
        warnings.append("公告/财报存在缺少或无法解析公开披露时间的记录，已排除以防止未来函数")
    if counts["after_cutoff"]:
        warnings.append("公告/财报包含信息截面之后公开的数据，已排除以防止未来函数")
    return DisclosureCutoffResult(
        eligible, excluded,
        {"status": "validated_with_warning" if warnings else "validated", "cutoff": cutoff_dt.isoformat(), "counts": counts, "warnings": warnings, "rules_version": DISCLOSURE_RULES_VERSION},
    )


def deduplicate_disclosures(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        value = dict(item)
        key = str(value.get("source_record_id") or "") or hashlib.sha256(
            "|".join(str(value.get(field) or "") for field in ("symbol", "report_type", "published_at", "title", "source_url")).encode("utf-8")
        ).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key); output.append(value)
    return output


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame): return data.copy()
    if isinstance(data, list): return pd.DataFrame(data)
    if isinstance(data, dict):
        for key in ("data", "results", "items", "announcements", "financial_reports"):
            if key in data and isinstance(data[key], (list, dict, pd.DataFrame)): return _as_dataframe(data[key])
        return pd.DataFrame(data)
    raise DisclosureNormalizationError("公告/财报数据必须是 DataFrame、list 或 dict")


def _resolve_mapping(columns: Any) -> dict[str, str]:
    names = {str(column): str(column) for column in columns}; mapping: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in names: mapping[canonical] = names[alias]; break
    return mapping


def _parse_date(value: Any) -> date | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce"); return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError): return None


def _parse_datetime(value: Any, default_tz: Any) -> datetime | None:
    if value is None or value == "": return None
    if isinstance(value, datetime): parsed = value
    elif isinstance(value, date): parsed = datetime.combine(value, time.max)
    else:
        raw = str(value).strip()
        if not raw: return None
        if raw.endswith("Z"): raw = raw[:-1] + "+00:00"
        try: parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = pd.to_datetime(raw, errors="coerce")
            if pd.isna(parsed): return None
            parsed = parsed.to_pydatetime()
    if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def _coerce_cutoff(value: date | datetime, tz: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=tz)
        return parsed.astimezone(timezone.utc)
    return datetime.combine(value, time.max, tzinfo=tz).astimezone(timezone.utc)


def _resolve_timezone(value: str) -> Any:
    try: return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc: raise ValueError(f"不支持的公告/财报时区: {value}") from exc


def _text(value: Any) -> str | None:
    if value is None: return None
    try:
        if pd.isna(value): return None
    except (TypeError, ValueError): pass
    return str(value).strip() or None


def _canonical_symbol(value: Any) -> str:
    text = str(value).strip().upper()
    if "." not in text and text.isdigit(): text = f"{text}.SZ" if text.startswith(("000", "001", "002", "003", "300", "301")) else f"{text}.SH"
    return text


def _exchange(symbol: str) -> str | None:
    return {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(symbol.rsplit(".", 1)[-1])


def _record_id(symbol: str, report_type: str, published: datetime | None, title: str, url: str, row_number: int) -> str:
    basis = "|".join((symbol, report_type, published.isoformat() if published else "", title, url, str(row_number)))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _concat_rejected(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts: return pd.DataFrame(columns=[*DISCLOSURE_COLUMNS, "_rejection_reason"])
    result = pd.concat(parts, ignore_index=True, sort=False)
    if "_rejection_reason" not in result.columns: result["_rejection_reason"] = "未注明"
    return result


