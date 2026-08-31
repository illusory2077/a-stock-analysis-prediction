from datetime import date

import pandas as pd

from src.market import TradingCalendar, compare_daily_bars, normalize_daily_bars, validate_observed_trade_dates


class CalendarProvider:
    name = "calendar-test"

    def trade_calendar(self, start_date, end_date, *, exchange="SSE"):
        return pd.DataFrame(
            [
                {"exchange": exchange, "cal_date": "2026-08-19", "is_open": 1},
                {"exchange": exchange, "cal_date": "2026-08-20", "is_open": 0},
            ]
        )


def _bars(close: float) -> pd.DataFrame:
    result = normalize_daily_bars(
        [{"symbol": "600519.SH", "trade_date": "2026-08-19", "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 100, "amount": 1000}],
        source="test",
    )
    # pandas concat/merge 会比较 attrs；测试只关注标准行情字段。
    result.attrs = {}
    return result


def test_trading_calendar_and_non_trading_date_validation() -> None:
    calendar = TradingCalendar(CalendarProvider())
    assert calendar.is_open(date(2026, 8, 19)) is True
    assert calendar.is_open(date(2026, 8, 20)) is False
    result = validate_observed_trade_dates(
        pd.concat([_bars(100), _bars(101).assign(trade_date=date(2026, 8, 20))], ignore_index=True),
        open_days={date(2026, 8, 19)},
        requested_start=date(2026, 8, 19),
        requested_end=date(2026, 8, 20),
    )
    assert len(result.data) == 1
    assert len(result.rejected_data) == 1
    assert result.report["unexpected_non_trading_dates"] == ["2026-08-20"]


def test_cross_validator_reports_match_and_mismatch() -> None:
    matched = compare_daily_bars(_bars(100), _bars(100.2), primary_source="tushare", secondary_source="akshare")
    assert matched["status"] == "matched"
    mismatch = compare_daily_bars(_bars(100), _bars(102), primary_source="tushare", secondary_source="akshare")
    assert mismatch["status"] == "mismatch"
    assert mismatch["metrics"]["close"]["max_diff_pct"] > 0.5
