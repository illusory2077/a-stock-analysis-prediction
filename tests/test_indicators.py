from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.analysis import (
    PredictionInputBlockedError,
    TechnicalIndicatorError,
    calculate_technical_indicators,
)


def _bars(rows: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-01-05", periods=rows, freq="B")
    close = pd.Series(range(100, 100 + rows), dtype=float)
    return pd.DataFrame(
        {
            "symbol": "600519.SH",
            "exchange": "SSE",
            "asset_type": "stock",
            "trade_date": dates.date,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "frequency": "1d",
            "price_adjustment": "none",
            "source": "tushare",
            "retrieved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )


def _report(cross_status: str = "matched") -> dict:
    return {
        "status": "validated",
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "calendar_validation": {"status": "validated", "unexpected_non_trading_dates": []},
        "cross_validation": {"status": cross_status},
    }


def test_calculate_technical_indicators_computes_core_columns() -> None:
    result = calculate_technical_indicators(
        _bars(),
        _report(),
        symbol="600519.SH",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 4, 24),
    )

    assert result.quality_gate.status == "approved"
    assert len(result.data) == 80
    assert {"ma_5", "ma_20", "ma_60", "macd_dif", "macd_dea", "macd_hist"}.issubset(result.data.columns)
    assert {"rsi_6", "rsi_14", "bollinger_upper_20", "bollinger_lower_20", "atr_14"}.issubset(result.data.columns)
    assert {"support_20", "resistance_20", "support_60", "resistance_60"}.issubset(result.data.columns)
    assert result.data.iloc[-1]["ma_5"] == pytest.approx(177.0)
    assert result.data.iloc[-1]["rsi_14"] == pytest.approx(100.0)
    assert result.data.iloc[-1]["support_20"] == pytest.approx(158.0)
    assert result.data.iloc[-1]["resistance_20"] == pytest.approx(181.0)
    assert result.summary["latest_trade_date"] == "2026-04-24"


def test_calculate_technical_indicators_uses_only_prior_rows_for_earlier_values() -> None:
    original = _bars(40)
    changed_future = original.copy()
    changed_future.loc[39, "close"] = 10000

    left = calculate_technical_indicators(original, _report(), symbol="600519.SH").data
    right = calculate_technical_indicators(changed_future, _report(), symbol="600519.SH").data

    for column in ("ma_5", "macd_dif", "rsi_14", "bollinger_upper_20", "atr_14", "support_20"):
        assert left.loc[30, column] == pytest.approx(right.loc[30, column])


def test_calculate_technical_indicators_allows_degraded_gate_and_propagates_warning() -> None:
    result = calculate_technical_indicators(
        _bars(25),
        _report(cross_status="unavailable"),
        symbol="600519.SH",
        route_degraded=True,
    )

    assert result.quality_gate.status == "degraded"
    assert any("unavailable" in warning for warning in result.summary["warnings"])
    assert result.data["ma_60"].isna().all()


def test_calculate_technical_indicators_rejects_blocked_input_before_calculation() -> None:
    with pytest.raises(PredictionInputBlockedError):
        calculate_technical_indicators(
            _bars(),
            _report(cross_status="mismatch"),
            symbol="600519.SH",
        )


def test_calculate_technical_indicators_validates_parameters() -> None:
    with pytest.raises(TechnicalIndicatorError, match="窗口"):
        calculate_technical_indicators(
            _bars(),
            _report(),
            symbol="600519.SH",
            ma_windows=(0,),
        )
