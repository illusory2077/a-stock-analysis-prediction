from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.analysis import (
    PredictionInputBlockedError,
    TechnicalSignalError,
    calculate_technical_indicators,
    generate_technical_signals,
)
from src.analysis.quality_gate import QualityGateResult


def _bars(periods: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-01-05", periods=periods, freq="B")
    close = pd.Series(range(100, 100 + periods), dtype=float)
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
            "source": "test",
            "retrieved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )


def _report(*, cross_status: str = "matched") -> dict:
    return {
        "status": "validated",
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "calendar_validation": {"status": "validated"},
        "cross_validation": {"status": cross_status},
    }


def test_generate_technical_signals_returns_explainable_latest_signal() -> None:
    indicators = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")

    result = generate_technical_signals(indicators, symbol="600519.SH")

    assert result.quality_gate.status == "approved"
    assert result.summary["direction"] == "bullish"
    assert result.summary["composite_score"] >= 2
    assert result.summary["signal_strength"] > 0
    assert 0 < result.summary["confidence"] <= 1
    assert result.summary["latest_trade_date"] == "2026-04-24"
    assert result.latest["signal_direction"] == "bullish"
    assert "trend_signal" in result.data.columns
    assert result.latest["signal_triggers"]
    assert result.latest["signal_invalidations"]


def test_generate_technical_signals_propagates_degraded_gate_and_reduces_confidence() -> None:
    indicators = calculate_technical_indicators(
        _bars(), _report(cross_status="unavailable"), symbol="600519.SH", route_degraded=True
    )
    result = generate_technical_signals(indicators)

    assert result.quality_gate.status == "degraded"
    assert result.summary["confidence"] < 1
    assert any("unavailable" in warning for warning in result.summary["warnings"])


def test_generate_technical_signals_rejects_blocked_gate() -> None:
    indicators = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")
    blocked_gate = QualityGateResult(
        status="blocked",
        can_predict=False,
        reasons=("测试阻断",),
        warnings=(),
        checks={},
        data_cutoff="2026-04-24",
        checked_at="2026-08-31T00:00:00+00:00",
    )

    with pytest.raises(PredictionInputBlockedError, match="测试阻断"):
        generate_technical_signals(replace(indicators, quality_gate=blocked_gate))


def test_generate_technical_signals_does_not_use_future_rows() -> None:
    indicators = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")
    changed = _bars()
    changed.loc[79, "close"] = 10000
    changed.loc[79, "high"] = 10002
    changed.loc[79, "open"] = 9999
    changed.loc[79, "low"] = 9998
    changed_indicators = calculate_technical_indicators(changed, _report(), symbol="600519.SH")

    left = generate_technical_signals(indicators).data
    right = generate_technical_signals(changed_indicators).data
    columns = ["trend_signal", "macd_signal", "bollinger_signal", "composite_score", "signal_direction"]
    pd.testing.assert_frame_equal(left.loc[[60], columns], right.loc[[60], columns])


def test_generate_technical_signals_validates_input_and_thresholds() -> None:
    indicators = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")

    with pytest.raises(TechnicalSignalError, match="阈值"):
        generate_technical_signals(indicators, rsi_overbought=20, rsi_oversold=30)
    with pytest.raises(TechnicalSignalError, match="缺少信号所需字段"):
        generate_technical_signals(replace(indicators, data=indicators.data.drop(columns=["ma_60"])))
