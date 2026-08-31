from datetime import date

import pandas as pd

from src.data_providers import DataProvider, DataProviderError, DataSourceRouter
from src.market import TradingCalendar


class RoutedMarketProvider(DataProvider):
    def __init__(self, name: str, *, close: float = 100.0, fail_daily: bool = False, calendar: bool = True) -> None:
        self.name = name
        self.close = close
        self.fail_daily = fail_daily
        self.calendar_enabled = calendar
        self.calendar_calls = 0

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": not self.fail_daily}

    def daily_bars(self, symbol, start_date, end_date):
        if self.fail_daily:
            raise DataProviderError(f"{self.name} unavailable")
        return [
            {
                "symbol": symbol,
                "trade_date": start_date,
                "open": self.close - 1,
                "high": self.close + 1,
                "low": self.close - 2,
                "close": self.close,
                "volume": 100,
                "amount": 1000,
            }
        ]

    def trade_calendar(self, start_date, end_date, *, exchange="SSE"):
        if not self.calendar_enabled:
            raise DataProviderError("calendar unavailable")
        self.calendar_calls += 1
        return pd.DataFrame(
            [
                {"exchange": exchange, "cal_date": start_date.isoformat(), "is_open": 1},
                {"exchange": exchange, "cal_date": "2026-08-20", "is_open": 0},
            ]
        )


def test_trading_calendar_caches_same_range() -> None:
    provider = RoutedMarketProvider("calendar")
    calendar = TradingCalendar(provider)
    start = date(2026, 8, 19)
    assert calendar.open_days(start, start) == {start}
    assert calendar.open_days(start, start) == {start}
    assert provider.calendar_calls == 1


def test_router_filters_known_non_trading_rows() -> None:
    provider = RoutedMarketProvider("primary")
    original = provider.daily_bars

    def daily_bars(symbol, start_date, end_date):
        rows = original(symbol, start_date, end_date)
        rows.append({**rows[0], "trade_date": date(2026, 8, 20), "close": 101, "open": 100, "high": 102, "low": 99})
        return rows

    provider.daily_bars = daily_bars
    result = DataSourceRouter(market_providers=[provider], news_providers=[]).fetch_daily_bars(
        "600519.SH", date(2026, 8, 19), date(2026, 8, 20)
    )
    assert len(result["data"]) == 1
    assert len(result["rejected_data"]) == 1
    assert result["quality_report"]["calendar_validation"]["unexpected_non_trading_dates"] == ["2026-08-20"]
    assert result["quality_report"]["output_rows"] == 1
    assert result["quality_report"]["rejected_rows"] == 1


def test_router_cross_validates_secondary_and_keeps_primary_on_mismatch() -> None:
    primary = RoutedMarketProvider("tushare", close=100)
    secondary = RoutedMarketProvider("akshare", close=102, calendar=False)
    result = DataSourceRouter(market_providers=[primary, secondary], news_providers=[]).fetch_daily_bars(
        "600519.SH", date(2026, 8, 19), date(2026, 8, 19)
    )
    assert result["source"] == "tushare"
    assert result["data"].iloc[0]["close"] == 100
    assert result["quality_report"]["cross_validation"]["status"] == "mismatch"
    assert result["quality_report"]["status"] == "validated_with_warning"
    assert any(item["role"] == "secondary_validation" and item["ok"] for item in result["attempts"])
    assert result["secondary_audits"][0]["source"] == "akshare"
    assert result["secondary_audits"][0]["status"] == "mismatch"
    assert result["secondary_audits"][0]["raw_data"][0]["close"] == 102


def test_router_marks_unavailable_secondary_without_discarding_primary() -> None:
    primary = RoutedMarketProvider("tushare", close=100)
    secondary = RoutedMarketProvider("akshare", fail_daily=True, calendar=False)
    result = DataSourceRouter(market_providers=[primary, secondary], news_providers=[]).fetch_daily_bars(
        "600519.SH", date(2026, 8, 19), date(2026, 8, 19)
    )
    assert len(result["data"]) == 1
    assert result["quality_report"]["cross_validation"]["status"] == "unavailable"
    assert result["quality_report"]["cross_validation"]["details"][0]["secondary_source"] == "akshare"
    assert result["secondary_audits"][0]["status"] == "unavailable"
    assert result["secondary_audits"][0]["raw_data"] is None
    assert result["secondary_audits"][0]["error"] == "akshare unavailable"
