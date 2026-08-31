from datetime import date

import pandas as pd

from src.analysis import evaluate_market_environment
from src.data_providers import DataProvider, DataSourceRouter


class IndexProvider(DataProvider):
    name = "test-index"

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": True}

    def daily_bars(self, symbol, start_date, end_date):
        raise AssertionError("指数测试不应调用 daily_bars")

    def index_daily_bars(self, symbol, start_date, end_date):
        return [
            {
                "ts_code": symbol,
                "trade_date": start_date,
                "open": 99,
                "high": 101,
                "low": 98,
                "close": 100,
                "vol": 1000,
                "amount": 100000,
            }
        ]


def _index_frame(start: int, *, symbol: str = "000001.SH") -> pd.DataFrame:
    dates = pd.date_range("2026-07-01", periods=25, freq="B")
    close = pd.Series(range(start, start + 25), dtype=float)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "asset_type": "index",
            "trade_date": dates.date,
            "close": close,
            "source": "test",
        }
    )


def test_market_environment_scores_indices_and_breadth() -> None:
    result = evaluate_market_environment(
        {
            "000001.SH": _index_frame(100),
            "000300.SH": _index_frame(200, symbol="000300.SH"),
            "399006.SZ": _index_frame(300, symbol="399006.SZ"),
        },
        breadth={"advancing": 3800, "declining": 1200},
    )

    assert result.score is not None
    assert result.score > 0
    assert result.data_cutoff == "2026-08-04"
    assert result.to_dimension()["available"] is True
    assert result.summary["breadth"]["score"] > 0
    assert result.summary["indices"]["000001.SH"]["momentum_score"] == 1.0


def test_market_environment_marks_missing_inputs_unavailable() -> None:
    result = evaluate_market_environment({})

    assert result.score is None
    assert result.to_dimension()["available"] is False
    assert any("没有可用的指数行情" in warning for warning in result.summary["warnings"])


def test_router_fetch_index_bars_uses_index_adapter_and_normalizes() -> None:
    router = DataSourceRouter(
        market_providers=[IndexProvider()],
        news_providers=[],
        validate_calendar=False,
    )
    result = router.fetch_index_bars("000001.SH", date(2026, 8, 31), date(2026, 8, 31))

    assert result["source"] == "test-index"
    assert result["data"].iloc[0]["symbol"] == "000001.SH"
    assert result["data"].iloc[0]["asset_type"] == "index"
    assert result["quality_report"]["cross_validation"]["status"] == "skipped"
