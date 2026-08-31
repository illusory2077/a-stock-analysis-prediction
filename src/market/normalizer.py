"""行情数据标准化入口。"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from .schema import COLUMN_ALIASES, STANDARD_COLUMNS

_EMPTY_VALUES = {"", "-", "--", "n/a", "na", "null", "none", "nan", "\u2014", "\u65e0"}
_EXCHANGE_SUFFIXES = {
    "SH": "SSE",
    "SSE": "SSE",
    "SZ": "SZSE",
    "SZSE": "SZSE",
    "BJ": "BSE",
    "BSE": "BSE",
    "HK": "HKEX",
    "HKEX": "HKEX",
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "AMEX": "AMEX",
}


class MarketNormalizationError(ValueError):
    """行情输入无法转换为统一结构。"""


def normalize_daily_bars(
    data: Any,
    *,
    symbol: str | None = None,
    source: str = "unknown",
    retrieved_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    asset_type: str | None = None,
    currency: str | None = None,
    frequency: str = "1d",
    price_adjustment: str = "none",
    data_version: str | None = None,
) -> pd.DataFrame:
    """将供应商日线转换为标准 DataFrame。

    无法通过基础字段解析的行不会静默填 0，而是放到返回 DataFrame.attrs["rejected_data"]。
    质量规则的深度校验由 :func:`src.market.quality.validate_daily_bars` 完成。
    """
    frame = _as_dataframe(data)
    retrieved = _as_utc_timestamp(retrieved_at or datetime.now(timezone.utc))
    requested_symbol = _normalize_symbol(symbol) if symbol else None
    mapping = _resolve_mapping(frame.columns)
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    if frame.empty:
        result = _empty_frame()
        result.attrs.update({"rejected_data": pd.DataFrame(), "normalization_warnings": [], "column_mapping": mapping})
        return result

    for row_number, (_, raw_row) in enumerate(frame.iterrows(), start=1):
        raw = raw_row.to_dict()
        candidate = _canonicalize_row(raw, mapping)
        row_warnings: list[str] = []
        row_errors: list[str] = []

        raw_symbol = _first_value(candidate.get("symbol"), requested_symbol)
        canonical_symbol = _normalize_symbol(raw_symbol)
        if requested_symbol and canonical_symbol and _base_code(canonical_symbol) == _base_code(requested_symbol):
            canonical_symbol = requested_symbol
        if not canonical_symbol:
            row_errors.append("缺少或无法解析 symbol")
        exchange = _normalize_exchange(candidate.get("exchange"))
        inferred_exchange = _infer_exchange(canonical_symbol, asset_type=asset_type)
        if exchange is None:
            exchange = inferred_exchange
        if exchange is None:
            row_errors.append("无法根据代码推断 exchange")
        elif requested_symbol and _exchange_from_symbol(requested_symbol) and exchange != _exchange_from_symbol(requested_symbol):
            row_errors.append("exchange 与请求的 symbol 不一致")

        row_asset_type = _normalize_asset_type(_first_value(candidate.get("asset_type"), asset_type))
        if row_asset_type is None:
            row_asset_type = _infer_asset_type(canonical_symbol, exchange)
        if row_asset_type is None:
            row_errors.append("缺少或无法推断 asset_type")
        row_currency = _normalize_currency(_first_value(candidate.get("currency"), currency))
        if row_currency is None:
            row_currency = _infer_currency(exchange)
            if row_currency is None:
                row_warnings.append("缺少 currency")

        trade_date = _parse_trade_date(candidate.get("trade_date"))
        if trade_date is None:
            row_errors.append("缺少或无法解析 trade_date")
        else:
            if start_date and trade_date < start_date:
                row_errors.append(f"trade_date 早于请求范围 {start_date.isoformat()}")
            if end_date and trade_date > end_date:
                row_errors.append(f"trade_date 晚于请求范围 {end_date.isoformat()}")

        values: dict[str, Any] = {}
        for field in ("open", "high", "low", "close"):
            parsed = _parse_number(candidate.get(field))
            if parsed is None:
                row_errors.append(f"缺少或无法解析 {field}")
            elif parsed < 0:
                row_errors.append(f"{field} 不能为负数")
            values[field] = parsed
        for field in ("volume", "amount", "adjust_factor"):
            parsed = _parse_number(candidate.get(field))
            if parsed is not None and parsed < 0:
                row_errors.append(f"{field} 不能为负数")
            if parsed is None and _has_value(candidate.get(field)):
                row_warnings.append(f"{field} 无法解析，保留为空")
            values[field] = parsed

        timestamp = _parse_timestamp(candidate.get("timestamp"))
        if timestamp is None and _has_value(candidate.get("timestamp")):
            row_warnings.append("timestamp 无法解析，保留为空")
        if not _has_value(candidate.get("volume")):
            row_warnings.append("缺少 volume")
        if not _has_value(candidate.get("amount")):
            row_warnings.append("缺少 amount")
        if not _has_value(candidate.get("adjust_factor")):
            row_warnings.append("缺少 adjust_factor")

        if row_errors:
            rejected.append({**raw, "_row_number": row_number, "_rejection_reason": "; ".join(row_errors)})
            warnings.extend(f"第 {row_number} 行：{item}" for item in row_warnings)
            continue

        standard = {
            "symbol": canonical_symbol,
            "exchange": exchange,
            "asset_type": row_asset_type,
            "trade_date": trade_date,
            "timestamp": timestamp,
            **values,
            "currency": row_currency,
            "source": str(source),
            "source_record_id": _string_or_none(candidate.get("source_record_id")),
            "retrieved_at": retrieved,
            "data_version": _string_or_none(_first_value(candidate.get("data_version"), data_version)),
            "frequency": str(frequency or "1d"),
            # 当前内部行情口径固定为未复权，避免供应商默认口径混用。
            "price_adjustment": "none",
        }
        extras = {
            f"extra_{key}": value
            for key, value in raw.items()
            if key not in mapping and not str(key).startswith("_")
        }
        standard.update(extras)
        rows.append(standard)
        warnings.extend(f"第 {row_number} 行：{item}" for item in row_warnings)

    result = pd.DataFrame(rows)
    if result.empty:
        result = _empty_frame()
    else:
        result = _order_columns(result)
    rejected_frame = pd.DataFrame(rejected)
    result.attrs.update(
        {
            "rejected_data": rejected_frame,
            "normalization_warnings": _unique(warnings),
            "column_mapping": mapping,
            "input_rows": len(frame),
        }
    )
    return result


def _as_dataframe(data: Any) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame()
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, dict):
        if isinstance(data.get("data"), (list, tuple, dict, pd.DataFrame)):
            return _as_dataframe(data["data"])
        if isinstance(data.get("results"), (list, tuple, dict, pd.DataFrame)):
            return _as_dataframe(data["results"])
        return pd.DataFrame([data])
    if isinstance(data, (list, tuple)):
        return pd.DataFrame(data)
    raise MarketNormalizationError(f"不支持的行情数据类型: {type(data).__name__}")


def _resolve_mapping(columns: Any) -> dict[str, str]:
    normalized = {str(column).strip().lower(): str(column) for column in columns}
    mapping: dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                mapping[target] = normalized[alias.lower()]
                break
    return mapping


def _canonicalize_row(raw: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = {field: raw.get(column) for field, column in mapping.items()}
    return result


def _normalize_symbol(value: Any) -> str | None:
    if not _has_value(value):
        return None
    text = str(value).strip().upper().replace(" ", "")
    text = text.replace("-", ".")
    match = re.fullmatch(r"([A-Z]+)?[.]?([0-9A-Z]{1,16})(?:[.]([A-Z]+))?", text)
    if not match:
        return text or None
    prefix, code, suffix = match.groups()
    market = suffix or prefix
    if market in _EXCHANGE_SUFFIXES:
        return f"{code}.{market}"
    if prefix and prefix not in _EXCHANGE_SUFFIXES:
        return text
    return code


def _base_code(symbol: str | None) -> str:
    return (symbol or "").split(".", 1)[0]


def _exchange_from_symbol(symbol: str | None) -> str | None:
    if not symbol or "." not in symbol:
        return None
    return _EXCHANGE_SUFFIXES.get(symbol.rsplit(".", 1)[1])


def _infer_exchange(symbol: str | None, *, asset_type: str | None = None) -> str | None:
    code = _base_code(symbol)
    if _exchange_from_symbol(symbol):
        return _exchange_from_symbol(symbol)
    if not code.isdigit():
        return {"USD": "NYSE"}.get(code)
    if asset_type and asset_type.lower() == "index":
        if code.startswith("399"):
            return "SZSE"
        if code.startswith(("000", "000001")):
            return "SSE"
    if code.startswith(("600", "601", "603", "605", "688")):
        return "SSE"
    if code.startswith(("000", "001", "002", "003", "300")):
        return "SZSE"
    if code.startswith(("430", "830", "870", "920")):
        return "BSE"
    if code.startswith("399"):
        return "SZSE"
    return None


def _infer_asset_type(symbol: str | None, exchange: str | None) -> str | None:
    code = _base_code(symbol)
    if not code:
        return None
    if code.startswith("399") and exchange in {"SSE", "SZSE"}:
        return "index"
    if code.startswith(("159", "510", "511", "512", "513", "515", "516", "518")):
        return "etf"
    if exchange in {"SSE", "SZSE", "BSE"}:
        return "stock"
    if exchange in {"NYSE", "NASDAQ", "AMEX"}:
        return "stock"
    return None


def _normalize_exchange(value: Any) -> str | None:
    if not _has_value(value):
        return None
    text = str(value).strip().upper().replace("交易所", "")
    return _EXCHANGE_SUFFIXES.get(text, text if text else None)


def _normalize_asset_type(value: Any) -> str | None:
    if not _has_value(value):
        return None
    text = str(value).strip().lower()
    aliases = {"股票": "stock", "指数": "index", "基金": "etf", "交易所交易基金": "etf", "外汇": "fx", "数字货币": "crypto"}
    return aliases.get(text, text)


def _normalize_currency(value: Any) -> str | None:
    if not _has_value(value):
        return None
    text = str(value).strip().upper()
    return {"人民币": "CNY", "元": "CNY", "美元": "USD", "$": "USD", "港币": "HKD"}.get(text, text)


def _infer_currency(exchange: str | None) -> str | None:
    if exchange in {"SSE", "SZSE", "BSE"}:
        return "CNY"
    if exchange in {"NYSE", "NASDAQ", "AMEX"}:
        return "USD"
    if exchange == "HKEX":
        return "HKD"
    return None


def _parse_trade_date(value: Any) -> date | None:
    if not _has_value(value):
        return None
    try:
        parsed = pd.to_datetime(str(value).strip(), errors="raise")
        return parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    if not _has_value(value):
        return None
    try:
        parsed = pd.Timestamp(value)
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize("Asia/Shanghai")
        return parsed.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return None


def _as_utc_timestamp(value: datetime | pd.Timestamp) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _parse_number(value: Any) -> float | None:
    if not _has_value(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "")
    try:
        parsed = float(text)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    return str(value).strip() if _has_value(value) else None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return str(value).strip().lower() not in _EMPTY_VALUES


def _first_value(*values: Any) -> Any:
    for value in values:
        if _has_value(value):
            return value
    return None


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _order_columns(frame: pd.DataFrame) -> pd.DataFrame:
    extras = [column for column in frame.columns if column not in STANDARD_COLUMNS]
    return frame[[*STANDARD_COLUMNS, *sorted(extras)]]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))



