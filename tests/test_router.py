from datetime import date

from src.data_providers import DataProvider, DataProviderError, DataSourceRouter


class FailingProvider(DataProvider):
    name = "primary"

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": False}

    def daily_bars(self, symbol, start_date, end_date):
        raise DataProviderError("429 rate limit")


class WorkingProvider(DataProvider):
    name = "backup"

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": True}

    def daily_bars(self, symbol, start_date, end_date):
        return [{"symbol": symbol, "close": 100}]


def test_router_falls_back_after_retryable_failure() -> None:
    router = DataSourceRouter(
        market_providers=[FailingProvider(), WorkingProvider()],
        news_providers=[],
        sleep_fn=lambda _: None,
    )
    result = router.fetch_daily_bars("600519.SH", date(2026, 8, 19), date(2026, 8, 19))
    assert result["source"] == "backup"
    assert result["degraded"] is True
    assert result["data"][0]["close"] == 100
    assert result["attempts"][0]["ok"] is False
    assert result["attempts"][-1]["ok"] is True
