"""融资融券数据的标准化、质量检查和主备交叉验证。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

MARGIN_RULES_VERSION = "1.1"
MARGIN_COLUMNS = [
    "symbol", "exchange", "asset_type", "trade_date", "timestamp",
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "short_balance", "short_position_volume", "short_sell_volume", "short_repay_volume",
    "combined_balance",
    "currency", "source", "source_record_id", "retrieved_at",
    "data_version", "frequency", "data_status",
]

_ALIASES = {
    "symbol": ("symbol", "ts_code", "代码", "股票代码", "证券代码"),
    "trade_date": ("trade_date", "日期", "交易日期", "交易日", "date"),
    "timestamp": ("timestamp", "时间戳", "datetime", "时间"),
    "margin_balance": ("margin_balance", "rzye", "融资余额", "融资余额(元)", "融资余额（元）"),
    "margin_buy_amount": ("margin_buy_amount", "rzmre", "融资买入额", "融资买入额(元)", "融资买入额（元）"),
    "margin_repay_amount": ("margin_repay_amount", "rzche", "融资偿还额", "融资偿还额(元)", "融资偿还额（元）"),
    "short_balance": ("short_balance", "rqye", "融券余额", "融券余额(元)", "融券余额（元）"),
    "short_position_volume": ("short_position_volume", "rqyl", "融券余量", "融券余量(股)", "融券余量（股）"),
    "short_sell_volume": ("short_sell_volume", "short_sell_amount", "rqmcl", "融券卖出量", "融券卖出量(股)", "融券卖出额"),
    "short_repay_volume": ("short_repay_volume", "short_repay_amount", "rqchl", "rqche", "融券偿还量", "融券偿还量(股)", "融券偿还额"),
    "combined_balance": ("combined_balance", "rzrqye", "融资融券余额", "融资融券余额(元)", "融资融券余额（元）"),
    "source_record_id": ("source_record_id", "record_id", "id"),
}
_NUMERIC = ("margin_balance", "margin_buy_amount", "margin_repay_amount", "short_balance", "short_position_volume", "short_sell_volume", "short_repay_volume", "combined_balance")


@dataclass(frozen=True)
class MarginQualityResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


class MarginNormalizationError(ValueError):
    """融资融券输入无法转换为统一结构。"""


def normalize_margin(
    data: Any,
    *,
    symbol: str,
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
        row: dict[str, Any] = {
            "symbol": requested_symbol,
            "exchange": _exchange(requested_symbol),
            "asset_type": "stock",
            "trade_date": _parse_date(canonical.get("trade_date")),
            "timestamp": _parse_timestamp(canonical.get("timestamp")),
            **{field: _number(canonical.get(field)) for field in _NUMERIC},
            "currency": "CNY",
            "source": source,
            "source_record_id": _text(canonical.get("source_record_id")) or f"{source}:{requested_symbol}:{row_number}",
            "retrieved_at": retrieved,
            "data_version": data_version,
            "frequency": "1d",
            "data_status": "normalized",
        }
        errors: list[str] = []
        if row["trade_date"] is None:
            errors.append("缺少或无法解析 trade_date")
        elif start_date and row["trade_date"] < start_date:
            errors.append(f"trade_date 早于请求范围 {start_date.isoformat()}")
        elif end_date and row["trade_date"] > end_date:
            errors.append(f"trade_date 晚于请求范围 {end_date.isoformat()}")
        if all(row[field] is None for field in _NUMERIC):
            errors.append("融资融券余额和变动字段全部为空")
        for field in _NUMERIC:
            value = row[field]
            if value is not None and (not math.isfinite(value) or value < 0):
                errors.append(f"{field} 不是非负有限数字")
        if errors:
            row["data_status"] = "rejected"
            row["_rejection_reason"] = "; ".join(errors)
            rejected.append(row)
        else:
            rows.append(row)

    result = pd.DataFrame(rows, columns=MARGIN_COLUMNS)
    rejected_frame = pd.DataFrame(rejected, columns=[*MARGIN_COLUMNS, "_rejection_reason"])
    result.attrs.update({"rejected_data": rejected_frame, "input_rows": len(frame), "normalization_warnings": warnings, "column_mapping": mapping})
    return result


def validate_margin(
    data: pd.DataFrame,
    *,
    rejected_data: pd.DataFrame | None = None,
    input_rows: int | None = None,
    warnings: list[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> MarginQualityResult:
    if not isinstance(data, pd.DataFrame):
        raise MarginNormalizationError("融资融券标准化结果必须是 DataFrame")
    frame = data.copy()
    report_warnings = list(warnings or [])
    errors: list[str] = []
    rejected_parts = [rejected_data.copy()] if isinstance(rejected_data, pd.DataFrame) and not rejected_data.empty else []
    duplicate_count = 0
    for field in ("trade_date", *_NUMERIC):
        if field in frame.columns and field != "trade_date":
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
    if "trade_date" not in frame.columns:
        errors.append("缺少 trade_date")
        frame = pd.DataFrame(columns=MARGIN_COLUMNS)
    else:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        invalid_dates = frame["trade_date"].isna()
        if invalid_dates.any():
            bad = frame.loc[invalid_dates].copy(); bad["_rejection_reason"] = "trade_date 无效"
            rejected_parts.append(bad); frame = frame.loc[~invalid_dates].copy()
            report_warnings.append(f"发现无效交易日 {len(bad)} 条，已拒绝")
    missing_values = [field for field in _NUMERIC if field in frame.columns and frame[field].notna().any() and (frame[field] < 0).any()]
    for field in missing_values:
        bad_mask = frame[field] < 0; bad = frame.loc[bad_mask].copy(); bad["_rejection_reason"] = f"{field} 为负数"
        rejected_parts.append(bad); frame = frame.loc[~bad_mask].copy(); report_warnings.append(f"发现负的 {field} {len(bad)} 条，已拒绝")
    key_columns = [field for field in ("symbol", "trade_date", "frequency", "source") if field in frame.columns]
    if key_columns:
        duplicate_mask = frame.duplicated(key_columns, keep="first")
        if duplicate_mask.any():
            duplicate_count = int(duplicate_mask.sum()); frame = frame.loc[~duplicate_mask].copy()
            report_warnings.append(f"删除重复融资融券记录 {duplicate_count} 条")
    if not frame.empty:
        frame = frame.sort_values([field for field in ("symbol", "trade_date") if field in frame.columns], kind="stable").reset_index(drop=True)
    rejected = _concat_rejected(rejected_parts, frame.columns)
    total_input = input_rows if input_rows is not None else len(frame) + len(rejected)
    status = "validated_with_warning" if report_warnings or not rejected.empty or errors else "validated"
    if frame.empty: status = "rejected"
    report = {
        "margin_rules_version": MARGIN_RULES_VERSION, "status": status,
        "input_rows": total_input, "output_rows": len(frame), "rejected_rows": len(rejected),
        "duplicates_removed": duplicate_count, "warnings": list(dict.fromkeys(report_warnings)),
        "errors": list(dict.fromkeys(errors)), "column_mapping": column_mapping or {}, "data_range": {},
    }
    return MarginQualityResult(frame, rejected, report)


def compare_margin(primary: pd.DataFrame, secondary: pd.DataFrame, *, primary_source: str, secondary_source: str, amount_threshold: float = 0.2) -> dict[str, Any]:
    if primary.empty or secondary.empty:
        return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备融资融券数据为空"]}
    fields = [field for field in _NUMERIC if field in primary.columns and field in secondary.columns]
    left = primary[["trade_date", *fields]].drop_duplicates("trade_date").copy()
    right = secondary[["trade_date", *fields]].drop_duplicates("trade_date").copy()
    merged = left.merge(right, on="trade_date", how="inner", suffixes=("_primary", "_secondary"))
    if merged.empty:
        return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备融资融券没有共同交易日"]}
    metrics: dict[str, Any] = {}; max_diff = 0.0
    for field in fields:
        a = pd.to_numeric(merged[f"{field}_primary"], errors="coerce"); b = pd.to_numeric(merged[f"{field}_secondary"], errors="coerce")
        denominator = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1).replace(0, 1.0)
        diff = ((a - b).abs() / denominator).fillna(0.0); current = float(diff.max()) if len(diff) else 0.0; max_diff = max(max_diff, current)
        metrics[field] = {"max_diff_pct": round(current * 100, 6), "threshold_pct": amount_threshold * 100}
    status = "matched" if max_diff <= amount_threshold else "mismatch"
    warnings = [] if status == "matched" else [f"主备融资融券最大相对差异 {max_diff:.2%}，超过阈值 {amount_threshold:.2%}"]
    return {"status": status, "primary_source": primary_source, "secondary_source": secondary_source, "common_rows": len(merged), "latest_common_trade_date": str(merged["trade_date"].max()), "metrics": metrics, "warnings": warnings}


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame): return data.copy()
    if isinstance(data, list): return pd.DataFrame(data)
    if isinstance(data, dict):
        for key in ("data", "results", "margin", "margin_detail"):
            if key in data and isinstance(data[key], (list, dict, pd.DataFrame)): return _as_dataframe(data[key])
        return pd.DataFrame(data)
    raise MarginNormalizationError("融资融券数据必须是 DataFrame、list 或 dict")


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


def _parse_timestamp(value: Any) -> str | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce", utc=True); return None if pd.isna(parsed) else parsed.isoformat()
    except (TypeError, ValueError, OverflowError): return None


def _number(value: Any) -> float | None:
    try:
        result = float(value); return result if math.isfinite(result) else None
    except (TypeError, ValueError): return None


def _text(value: Any) -> str | None:
    if value is None: return None
    try:
        if pd.isna(value): return None
    except (TypeError, ValueError): pass
    return str(value)


def _canonical_symbol(value: Any) -> str:
    text = str(value).strip().upper()
    if "." not in text and text.isdigit(): text = f"{text}.SZ" if text.startswith(("000", "001", "002", "003", "300", "301")) else f"{text}.SH"
    return text


def _exchange(symbol: str) -> str | None:
    return {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(symbol.rsplit(".", 1)[-1])


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _concat_rejected(parts: list[pd.DataFrame], columns: Any) -> pd.DataFrame:
    if not parts: return pd.DataFrame(columns=[*columns, "_rejection_reason"])
    result = pd.concat(parts, ignore_index=True, sort=False)
    if "_rejection_reason" not in result.columns: result["_rejection_reason"] = "未注明"
    return result
