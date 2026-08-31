"""交易日历查询与行情交易日校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


class CalendarError(RuntimeError):
    """交易日历不可用或返回结构无法解析。"""


@dataclass
class CalendarValidationResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


class TradingCalendar:
    """通过数据源的交易日历接口查询开市日，并在内存中缓存查询结果。"""

    def __init__(self, provider: Any, *, exchange: str = "SSE") -> None:
        self.provider = provider
        self.exchange = exchange
        self._cache: dict[tuple[str, date, date], set[date]] = {}

    def open_days(self, start_date: date, end_date: date, *, exchange: str | None = None) -> set[date]:
        if start_date > end_date:
            raise CalendarError("交易日历范围无效：start_date 晚于 end_date")
        provider_method = getattr(self.provider, "trade_calendar", None)
        if provider_method is None:
            raise CalendarError(f"数据源 {getattr(self.provider, 'name', type(self.provider).__name__)} 不支持交易日历")
        selected_exchange = exchange or self.exchange
        cache_key = (selected_exchange, start_date, end_date)
        if cache_key in self._cache:
            return set(self._cache[cache_key])
        try:
            raw = provider_method(start_date, end_date, exchange=selected_exchange)
        except TypeError:
            # 兼容自定义测试 Provider 或旧适配器的三参数形式。
            raw = provider_method(start_date, end_date)
        except Exception as exc:  # noqa: BLE001
            raise CalendarError(f"获取 {selected_exchange} 交易日历失败: {exc}") from exc
        frame = _as_frame(raw)
        if frame.empty:
            self._cache[cache_key] = set()
            return set()
        date_column = _find_column(frame, ("cal_date", "trade_date", "日期", "交易日期", "date"))
        open_column = _find_column(frame, ("is_open", "开市", "是否交易", "交易状态"))
        if date_column is None or open_column is None:
            raise CalendarError("交易日历缺少 cal_date/trade_date 或 is_open 字段")
        open_days: set[date] = set()
        for _, row in frame.iterrows():
            parsed = _parse_date(row[date_column])
            if parsed and _is_open(row[open_column]):
                open_days.add(parsed)
        self._cache[cache_key] = open_days
        return set(open_days)

    def is_open(self, value: date, *, exchange: str | None = None) -> bool:
        return value in self.open_days(value, value, exchange=exchange)


def validate_observed_trade_dates(
    data: pd.DataFrame,
    *,
    open_days: set[date],
    requested_start: date,
    requested_end: date,
) -> CalendarValidationResult:
    """拒绝已知非交易日行情；普通交易日缺口不补齐、不拒绝。"""
    frame = data.copy().reset_index(drop=True)
    frame.attrs = {}
    if frame.empty:
        return CalendarValidationResult(
            frame,
            pd.DataFrame(columns=[*frame.columns, "_rejection_reason"]),
            {
                "status": "validated",
                "requested_start": requested_start.isoformat(),
                "requested_end": requested_end.isoformat(),
                "open_days_count": len(open_days),
                "unexpected_non_trading_dates": [],
                "warnings": [],
            },
        )
    parsed_dates = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    bad_mask = ~parsed_dates.isin(open_days)
    rejected = frame.loc[bad_mask].copy()
    if not rejected.empty:
        rejected["_rejection_reason"] = "trade_date 落在交易日历的非交易日"
    valid = frame.loc[~bad_mask].copy().reset_index(drop=True)
    bad_dates = sorted(
        {value.isoformat() for value in parsed_dates[bad_mask] if value is not None and not pd.isna(value)}
    )
    report = {
        "status": "validated_with_warning" if bad_dates else "validated",
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "open_days_count": len(open_days),
        "unexpected_non_trading_dates": bad_dates,
        "warnings": [f"发现非交易日行情 {len(rejected)} 条，已拒绝"] if bad_dates else [],
    }
    return CalendarValidationResult(valid, rejected, report)


def _as_frame(data: Any) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, dict):
        if "data" in data:
            return _as_frame(data["data"])
        if "results" in data:
            return _as_frame(data["results"])
        return pd.DataFrame([data])
    if isinstance(data, (list, tuple)):
        return pd.DataFrame(data)
    raise CalendarError(f"不支持的交易日历返回类型: {type(data).__name__}")


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    columns = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate.lower() in columns:
            return columns[candidate.lower()]
    return None


def _parse_date(value: Any) -> date | None:
    try:
        return pd.to_datetime(value, errors="raise").date()
    except (TypeError, ValueError, OverflowError):
        return None


def _is_open(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "1.0", "true", "yes", "open", "交易", "开市"}
