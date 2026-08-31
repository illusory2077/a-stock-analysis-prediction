"""龙虎榜数据的标准化、质量检查和主备交叉验证。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

DRAGON_TIGER_RULES_VERSION = "1.1"
DRAGON_TIGER_COLUMNS = [
    "symbol", "exchange", "asset_type", "trade_date", "timestamp", "reason",
    "close", "change_pct", "turnover_rate", "net_buy_amount", "buy_amount",
    "sell_amount", "institution_net_amount", "top_broker_net_amount", "currency",
    "source", "source_record_id", "retrieved_at", "data_version", "frequency", "data_status",
]
_ALIASES = {
    "symbol": ("symbol", "ts_code", "SECURITY_CODE", "代码", "股票代码", "证券代码"),
    "trade_date": ("trade_date", "TRADE_DATE", "日期", "交易日期", "交易日", "date"),
    "timestamp": ("timestamp", "时间戳", "datetime", "时间"),
    "reason": ("reason", "EXPLAIN", "EXPLANATION", "上榜原因", "异动原因", "解读"),
    "close": ("close", "CLOSE_PRICE", "收盘价", "收盘"),
    "change_pct": ("change_pct", "pct_change", "CHANGE_RATE", "涨跌幅", "涨跌幅(%)", "涨跌幅（%）"),
    "turnover_rate": ("turnover_rate", "TURNOVERRATE", "换手率", "换手率(%)", "换手率（%）"),
    "net_buy_amount": ("net_buy_amount", "net_amount", "BILLBOARD_NET_AMT", "净买入额", "龙虎榜净买额", "净买额", "买卖净额"),
    "buy_amount": ("buy_amount", "l_buy", "BILLBOARD_BUY_AMT", "买入额", "买入金额", "龙虎榜买入额"),
    "sell_amount": ("sell_amount", "l_sell", "BILLBOARD_SELL_AMT", "卖出额", "卖出金额", "龙虎榜卖出额", "sell"),
    "institution_net_amount": ("institution_net_amount", "net_buy", "机构净买入", "机构买入净额"),
    "top_broker_net_amount": ("top_broker_net_amount", "l_net", "营业部净买入", "营业部净买额", "游资净买入"),
    "source_record_id": ("source_record_id", "record_id", "id"),
}
_MONEY = ("net_buy_amount", "buy_amount", "sell_amount", "institution_net_amount", "top_broker_net_amount")


@dataclass(frozen=True)
class DragonTigerQualityResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


class DragonTigerNormalizationError(ValueError):
    """龙虎榜输入无法转换为统一结构。"""


def normalize_dragon_tiger(data: Any, *, symbol: str, source: str = "unknown", retrieved_at: datetime | None = None, start_date: date | None = None, end_date: date | None = None, data_version: str | None = None) -> pd.DataFrame:
    frame = _as_dataframe(data); retrieved = _as_utc(retrieved_at or datetime.now(timezone.utc)).isoformat(); requested_symbol = _canonical_symbol(symbol)
    mapping = _resolve_mapping(frame.columns); rows: list[dict[str, Any]] = []; rejected: list[dict[str, Any]] = []
    for row_number, (_, raw_row) in enumerate(frame.iterrows(), start=1):
        raw = raw_row.to_dict(); canonical = {key: raw.get(column) for key, column in mapping.items()}
        row: dict[str, Any] = {"symbol": requested_symbol, "exchange": _exchange(requested_symbol), "asset_type": "stock", "trade_date": _parse_date(canonical.get("trade_date")), "timestamp": _parse_timestamp(canonical.get("timestamp")), "reason": _text(canonical.get("reason")), "close": _number(canonical.get("close")), "change_pct": _number(canonical.get("change_pct")), "turnover_rate": _number(canonical.get("turnover_rate")), **{field: _number(canonical.get(field)) for field in _MONEY}, "currency": "CNY", "source": source, "source_record_id": _text(canonical.get("source_record_id")) or f"{source}:{requested_symbol}:{row_number}", "retrieved_at": retrieved, "data_version": data_version, "frequency": "1d", "data_status": "normalized"}
        errors: list[str] = []
        if row["trade_date"] is None: errors.append("缺少或无法解析 trade_date")
        elif start_date and row["trade_date"] < start_date: errors.append(f"trade_date 早于请求范围 {start_date.isoformat()}")
        elif end_date and row["trade_date"] > end_date: errors.append(f"trade_date 晚于请求范围 {end_date.isoformat()}")
        if all(row[field] is None for field in ("net_buy_amount", "buy_amount", "sell_amount")): errors.append("龙虎榜净买入、买入额和卖出额全部为空")
        if row["close"] is not None and row["close"] < 0: errors.append("close 不能为负数")
        if row["turnover_rate"] is not None and (row["turnover_rate"] < 0 or row["turnover_rate"] > 1000): errors.append("turnover_rate 超出合理范围")
        for field in _MONEY:
            value = row[field]
            if value is not None and not math.isfinite(value): errors.append(f"{field} 不是有限数字")
        if errors:
            row["data_status"] = "rejected"; row["_rejection_reason"] = "; ".join(errors); rejected.append(row)
        else: rows.append(row)
    result = pd.DataFrame(rows, columns=DRAGON_TIGER_COLUMNS); rejected_frame = pd.DataFrame(rejected, columns=[*DRAGON_TIGER_COLUMNS, "_rejection_reason"])
    result.attrs.update({"rejected_data": rejected_frame, "input_rows": len(frame), "normalization_warnings": [], "column_mapping": mapping}); return result


def validate_dragon_tiger(data: pd.DataFrame, *, rejected_data: pd.DataFrame | None = None, input_rows: int | None = None, warnings: list[str] | None = None, column_mapping: dict[str, str] | None = None) -> DragonTigerQualityResult:
    if not isinstance(data, pd.DataFrame): raise DragonTigerNormalizationError("龙虎榜标准化结果必须是 DataFrame")
    frame = data.copy(); report_warnings = list(warnings or []); errors: list[str] = []; rejected_parts = [rejected_data.copy()] if isinstance(rejected_data, pd.DataFrame) and not rejected_data.empty else []; duplicate_count = 0
    if "trade_date" not in frame.columns:
        errors.append("缺少 trade_date"); frame = pd.DataFrame(columns=DRAGON_TIGER_COLUMNS)
    else:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date; invalid = frame["trade_date"].isna()
        if invalid.any():
            bad = frame.loc[invalid].copy(); bad["_rejection_reason"] = "trade_date 无效"; rejected_parts.append(bad); frame = frame.loc[~invalid].copy(); report_warnings.append(f"发现无效交易日 {len(bad)} 条，已拒绝")
    for field in ("close", "change_pct", "turnover_rate", *_MONEY):
        if field in frame.columns: frame[field] = pd.to_numeric(frame[field], errors="coerce")
    for field in ("close", "turnover_rate"):
        if field in frame.columns:
            invalid = frame[field].notna() & (frame[field] < 0)
            if invalid.any():
                bad = frame.loc[invalid].copy(); bad["_rejection_reason"] = f"{field} 为负数"; rejected_parts.append(bad); frame = frame.loc[~invalid].copy(); report_warnings.append(f"发现负的 {field} {len(bad)} 条，已拒绝")
    key = [field for field in ("symbol", "trade_date", "reason", "source") if field in frame.columns]
    if key:
        duplicate = frame.duplicated(key, keep="first")
        if duplicate.any(): duplicate_count = int(duplicate.sum()); frame = frame.loc[~duplicate].copy(); report_warnings.append(f"删除重复龙虎榜记录 {duplicate_count} 条")
    if not frame.empty: frame = frame.sort_values([field for field in ("symbol", "trade_date") if field in frame.columns], kind="stable").reset_index(drop=True)
    rejected = _concat_rejected(rejected_parts, frame.columns); total_input = input_rows if input_rows is not None else len(frame) + len(rejected); status = "validated_with_warning" if report_warnings or not rejected.empty or errors else "validated"
    if frame.empty: status = "rejected"
    report = {"dragon_tiger_rules_version": DRAGON_TIGER_RULES_VERSION, "status": status, "input_rows": total_input, "output_rows": len(frame), "rejected_rows": len(rejected), "duplicates_removed": duplicate_count, "warnings": list(dict.fromkeys(report_warnings)), "errors": list(dict.fromkeys(errors)), "column_mapping": column_mapping or {}, "data_range": {}}
    return DragonTigerQualityResult(frame, rejected, report)


def compare_dragon_tiger(primary: pd.DataFrame, secondary: pd.DataFrame, *, primary_source: str, secondary_source: str, amount_threshold: float = 0.2) -> dict[str, Any]:
    if primary.empty or secondary.empty: return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备龙虎榜数据为空"]}
    fields = [field for field in ("net_buy_amount", "buy_amount", "sell_amount") if field in primary.columns and field in secondary.columns and (primary[field].notna().any() or secondary[field].notna().any())]
    left = primary[["trade_date", *fields]].copy()
    right = secondary[["trade_date", *fields]].copy()
    left.attrs = {}
    right.attrs = {}
    left = left.groupby("trade_date", as_index=False).sum(numeric_only=True)
    right = right.groupby("trade_date", as_index=False).sum(numeric_only=True)
    left.attrs = {}
    right.attrs = {}
    merged = left.merge(right, on="trade_date", how="inner", suffixes=("_primary", "_secondary"))
    if merged.empty: return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备龙虎榜没有共同交易日"]}
    metrics: dict[str, Any] = {}; max_diff = 0.0
    for field in fields:
        a = merged[f"{field}_primary"]; b = merged[f"{field}_secondary"]; denominator = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1).replace(0, 1.0); diff = ((a - b).abs() / denominator).fillna(0.0); current = float(diff.max()); max_diff = max(max_diff, current); metrics[field] = {"max_diff_pct": round(current * 100, 6), "threshold_pct": amount_threshold * 100}
    status = "matched" if max_diff <= amount_threshold else "mismatch"; warnings = [] if status == "matched" else [f"主备龙虎榜最大相对差异 {max_diff:.2%}，超过阈值 {amount_threshold:.2%}"]
    return {"status": status, "primary_source": primary_source, "secondary_source": secondary_source, "common_rows": len(merged), "latest_common_trade_date": str(merged["trade_date"].max()), "metrics": metrics, "warnings": warnings}


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame): return data.copy()
    if isinstance(data, list): return pd.DataFrame(data)
    if isinstance(data, dict):
        for key in ("data", "results", "top_list", "top_inst", "dragon_tiger"):
            if key in data and isinstance(data[key], (list, dict, pd.DataFrame)): return _as_dataframe(data[key])
        return pd.DataFrame(data)
    raise DragonTigerNormalizationError("龙虎榜数据必须是 DataFrame、list 或 dict")


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
