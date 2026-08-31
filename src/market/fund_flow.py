"""资金流向数据的标准化、质量检查和主备交叉验证。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

FUND_FLOW_RULES_VERSION = "1.0"
FUND_FLOW_COLUMNS = [
    "symbol",
    "exchange",
    "asset_type",
    "trade_date",
    "timestamp",
    "net_flow_amount",
    "net_flow_pct",
    "super_large_net_amount",
    "large_net_amount",
    "medium_net_amount",
    "small_net_amount",
    "currency",
    "source",
    "source_record_id",
    "retrieved_at",
    "data_version",
    "frequency",
    "data_status",
]

_ALIASES = {
    "symbol": ("symbol", "ts_code", "代码", "股票代码", "证券代码"),
    "trade_date": ("trade_date", "日期", "交易日期", "交易日", "date"),
    "timestamp": ("timestamp", "时间戳", "行情时间", "datetime", "时间"),
    "net_flow_amount": ("net_flow_amount", "net_mf_amount", "主力净流入-净额", "主力净流入", "主力净额"),
    "net_flow_pct": ("net_flow_pct", "主力净流入-净占比", "主力净占比"),
    "super_large_net_amount": ("super_large_net_amount", "超大单净流入-净额", "超大单净额"),
    "large_net_amount": ("large_net_amount", "大单净流入-净额", "大单净额"),
    "medium_net_amount": ("medium_net_amount", "中单净流入-净额", "中单净额"),
    "small_net_amount": ("small_net_amount", "小单净流入-净额", "小单净额"),
    "source_record_id": ("source_record_id", "record_id", "id"),
}


@dataclass(frozen=True)
class FundFlowQualityResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


class FundFlowNormalizationError(ValueError):
    """资金流向输入无法转换为统一结构。"""


def normalize_fund_flow(
    data: Any,
    *,
    symbol: str,
    source: str = "unknown",
    retrieved_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    data_version: str | None = None,
) -> pd.DataFrame:
    """将 Tushare/AKShare 资金流向转换为统一的日频结构。"""
    frame = _as_dataframe(data)
    retrieved = _as_utc(retrieved_at or datetime.now(timezone.utc)).isoformat()
    requested_symbol = _canonical_symbol(symbol)
    mapping = _resolve_mapping(frame.columns)
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row_number, (_, raw_row) in enumerate(frame.iterrows(), start=1):
        raw = raw_row.to_dict()
        canonical = _canonical_row(raw, mapping)
        row: dict[str, Any] = {
            "symbol": requested_symbol,
            "exchange": _exchange(requested_symbol),
            "asset_type": "stock",
            "trade_date": _parse_date(canonical.get("trade_date")),
            "timestamp": _parse_timestamp(canonical.get("timestamp")),
            "net_flow_amount": _number(canonical.get("net_flow_amount")),
            "net_flow_pct": _number(canonical.get("net_flow_pct")),
            "super_large_net_amount": _number(canonical.get("super_large_net_amount")),
            "large_net_amount": _number(canonical.get("large_net_amount")),
            "medium_net_amount": _number(canonical.get("medium_net_amount")),
            "small_net_amount": _number(canonical.get("small_net_amount")),
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
        if row["net_flow_amount"] is None:
            errors.append("缺少或无法解析 net_flow_amount")
        if row["net_flow_pct"] is not None and abs(row["net_flow_pct"]) > 100:
            errors.append("net_flow_pct 超出合理范围 [-100, 100]")
        for field in ("net_flow_amount", "net_flow_pct", "super_large_net_amount", "large_net_amount", "medium_net_amount", "small_net_amount"):
            value = row[field]
            if value is not None and not math.isfinite(value):
                errors.append(f"{field} 不是有限数字")
        if errors:
            row["data_status"] = "rejected"
            row["_rejection_reason"] = "; ".join(errors)
            rejected.append(row)
        else:
            rows.append(row)

    result = pd.DataFrame(rows, columns=FUND_FLOW_COLUMNS)
    rejected_frame = pd.DataFrame(rejected, columns=[*FUND_FLOW_COLUMNS, "_rejection_reason"])
    result.attrs.update(
        {
            "rejected_data": rejected_frame,
            "input_rows": len(frame),
            "normalization_warnings": warnings,
            "column_mapping": mapping,
        }
    )
    return result


def validate_fund_flow(
    data: pd.DataFrame,
    *,
    rejected_data: pd.DataFrame | None = None,
    input_rows: int | None = None,
    warnings: list[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> FundFlowQualityResult:
    """检查资金流向的日期、主键、数值和重复记录。"""
    if not isinstance(data, pd.DataFrame):
        raise FundFlowNormalizationError("资金流向必须是 pandas DataFrame")
    frame = data.copy()
    rejected_parts = [part.copy() for part in (rejected_data, frame.attrs.get("rejected_data")) if isinstance(part, pd.DataFrame) and not part.empty]
    errors: list[str] = []
    report_warnings = list(warnings or [])
    duplicates_removed = 0

    required = {"symbol", "exchange", "asset_type", "trade_date", "net_flow_amount", "currency", "source", "retrieved_at", "frequency"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        return FundFlowQualityResult(
            pd.DataFrame(columns=frame.columns),
            _concat_rejected(rejected_parts, frame.columns),
            _report("rejected", input_rows or len(frame), 0, len(frame), 0, [*report_warnings, f"缺少必需字段: {', '.join(missing)}"], errors, column_mapping),
        )

    invalid_mask = frame["net_flow_amount"].map(lambda value: _number(value) is None)
    if invalid_mask.any():
        invalid = frame.loc[invalid_mask].copy()
        invalid["_rejection_reason"] = "net_flow_amount 无效"
        rejected_parts.append(invalid)
        frame = frame.loc[~invalid_mask].copy()
        errors.append(f"发现无效资金净流入记录 {len(invalid)} 条，已拒绝")

    key_columns = ["symbol", "trade_date", "frequency", "source"]
    duplicate_mask = frame.duplicated(key_columns, keep="first")
    if duplicate_mask.any():
        duplicates_removed = int(duplicate_mask.sum())
        frame = frame.loc[~duplicate_mask].copy()
        report_warnings.append(f"删除完全重复资金流向记录 {duplicates_removed} 条")

    if not frame.empty:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        frame = frame.sort_values(["symbol", "trade_date"], kind="stable").reset_index(drop=True)
    rejected = _concat_rejected(rejected_parts, frame.columns)
    total_input = input_rows if input_rows is not None else len(frame) + len(rejected)
    status = "validated_with_warning" if report_warnings or not rejected.empty or errors else "validated"
    if frame.empty:
        status = "rejected"
    return FundFlowQualityResult(
        frame,
        rejected,
        _report(status, total_input, len(frame), len(rejected), duplicates_removed, report_warnings, errors, column_mapping),
    )


def compare_fund_flow(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    *,
    primary_source: str,
    secondary_source: str,
    amount_threshold: float = 0.2,
) -> dict[str, Any]:
    """比较两路资金流向的共同交易日；金额差异使用相对最大值归一化。"""
    if primary.empty or secondary.empty:
        return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备资金流向为空"]}
    left = primary[["trade_date", "net_flow_amount"]].rename(columns={"net_flow_amount": "primary_amount"})
    right = secondary[["trade_date", "net_flow_amount"]].rename(columns={"net_flow_amount": "secondary_amount"})
    merged = left.merge(right, on="trade_date", how="inner")
    if merged.empty:
        return {"status": "unavailable", "primary_source": primary_source, "secondary_source": secondary_source, "warnings": ["主备资金流向没有共同交易日"]}
    denominator = merged[["primary_amount", "secondary_amount"]].abs().max(axis=1).replace(0, 1.0)
    diff_pct = ((merged["primary_amount"] - merged["secondary_amount"]).abs() / denominator).fillna(0.0)
    max_diff = float(diff_pct.max())
    status = "matched" if max_diff <= amount_threshold else "mismatch"
    warnings = [] if status == "matched" else [f"主备资金净流入最大相对差异 {max_diff:.2%}，超过阈值 {amount_threshold:.2%}"]
    return {
        "status": status,
        "primary_source": primary_source,
        "secondary_source": secondary_source,
        "common_rows": int(len(merged)),
        "latest_common_trade_date": str(merged["trade_date"].max()),
        "metrics": {"net_flow_amount": {"max_diff_pct": round(max_diff * 100, 6), "threshold_pct": amount_threshold * 100}},
        "warnings": warnings,
    }


def _report(status: str, input_rows: int, output_rows: int, rejected_rows: int, duplicates_removed: int, warnings: list[str], errors: list[str], column_mapping: dict[str, str] | None) -> dict[str, Any]:
    return {
        "fund_flow_rules_version": FUND_FLOW_RULES_VERSION,
        "status": status,
        "input_rows": input_rows,
        "output_rows": output_rows,
        "rejected_rows": rejected_rows,
        "duplicates_removed": duplicates_removed,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "column_mapping": column_mapping or {},
        "data_range": {},
    }


def _as_dataframe(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        if isinstance(data.get("data"), (list, dict, pd.DataFrame)):
            return _as_dataframe(data["data"])
        if isinstance(data.get("results"), (list, dict, pd.DataFrame)):
            return _as_dataframe(data["results"])
        return pd.DataFrame(data)
    raise FundFlowNormalizationError("资金流向必须是 DataFrame、list 或 dict")


def _resolve_mapping(columns: Any) -> dict[str, str]:
    names = {str(column): str(column) for column in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in names:
                mapping[canonical] = names[alias]
                break
    return mapping


def _canonical_row(raw: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = {key: raw.get(column) for key, column in mapping.items()}
    # Tushare moneyflow 的金额单位为万元；统一转换为人民币元。
    if any(key in raw for key in ("net_mf_amount", "buy_elg_amount", "sell_elg_amount")):
        for field in ("net_flow_amount", "super_large_net_amount", "large_net_amount", "medium_net_amount", "small_net_amount"):
            if result.get(field) is not None:
                result[field] = _number(result[field])
                if result[field] is not None:
                    result[field] *= 10000
        result["net_flow_amount"] = _first_number(raw.get("net_mf_amount"), result.get("net_flow_amount"))
        if result["net_flow_amount"] is not None:
            result["net_flow_amount"] *= 10000
        result["super_large_net_amount"] = _net_pair(raw, "elg")
        result["large_net_amount"] = _net_pair(raw, "lg")
        result["medium_net_amount"] = _net_pair(raw, "md")
        result["small_net_amount"] = _net_pair(raw, "sm")
    return result


def _net_pair(raw: dict[str, Any], suffix: str) -> float | None:
    buy = _number(raw.get(f"buy_{suffix}_amount"))
    sell = _number(raw.get(f"sell_{suffix}_amount"))
    if buy is None and sell is None:
        return None
    return round(((buy or 0.0) - (sell or 0.0)) * 10000, 6)


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _parse_date(value: Any) -> date | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_timestamp(value: Any) -> str | None:
    try:
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        return None if pd.isna(parsed) else parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _canonical_symbol(value: Any) -> str:
    text = str(value).strip().upper()
    if "." not in text and text.isdigit():
        text = f"{text}.SZ" if text.startswith(("000", "001", "002", "003", "300", "301", "399")) else f"{text}.SH"
    return text


def _exchange(symbol: str) -> str | None:
    suffix = symbol.rsplit(".", 1)[-1]
    return {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _concat_rejected(parts: list[pd.DataFrame], columns: Any) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=[*columns, "_rejection_reason"])
    result = pd.concat(parts, ignore_index=True, sort=False)
    if "_rejection_reason" not in result.columns:
        result["_rejection_reason"] = "未注明"
    return result
