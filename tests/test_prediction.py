from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from src.analysis import (
    NextDayPredictionError,
    PredictionInputBlockedError,
    calculate_technical_indicators,
    generate_next_day_prediction,
    generate_technical_signals,
)
from src.analysis.quality_gate import QualityGateResult
from tests.test_signals import _bars, _report


def _technical_signals(*, cross_status: str = "matched"):
    indicators = calculate_technical_indicators(
        _bars(), _report(cross_status=cross_status), symbol="600519.SH"
    )
    return generate_technical_signals(indicators, symbol="600519.SH")


def test_generate_next_day_prediction_returns_conditional_technical_only_forecast() -> None:
    result = generate_next_day_prediction(
        _technical_signals(),
        target_trade_date=date(2026, 4, 27),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    assert result.symbol == "600519.SH"
    assert result.data_cutoff == "2026-04-24"
    assert result.target_trade_date == "2026-04-27"
    assert result.summary["direction"] == "bullish"
    assert result.summary["coverage"] == pytest.approx(0.25)
    assert result.summary["confidence"] <= 0.40
    assert sum(result.summary["probabilities"].values()) == pytest.approx(1.0)
    assert result.summary["price_range"]["lower"] < result.summary["latest_close"] < result.summary["price_range"]["upper"]
    assert set(result.summary["missing_dimensions"]) == {"market_environment", "fund_flow", "news"}
    assert any("条件性估计" in warning for warning in result.summary["warnings"])


def test_generate_next_day_prediction_renormalizes_available_dimension_weights() -> None:
    result = generate_next_day_prediction(
        _technical_signals(),
        market_environment={"score": 0.8, "evidence": ["指数趋势偏强"]},
        fund_flow={"score": 0.6, "evidence": ["资金净流入"]},
        news={"score": -0.2, "evidence": ["存在负面公告风险"]},
    )

    components = result.summary["components"]
    assert all(components[name]["available"] for name in components)
    assert sum(item["effective_weight"] for item in components.values()) == pytest.approx(1.0)
    assert result.summary["coverage"] == pytest.approx(1.0)
    assert result.summary["confidence"] > 0.4
    assert result.summary["direction"] == "bullish"


def test_generate_next_day_prediction_propagates_degraded_gate() -> None:
    result = generate_next_day_prediction(_technical_signals(cross_status="unavailable"))

    assert result.quality_gate.status == "degraded"
    assert result.summary["confidence"] < 0.40
    assert any("degraded" in warning for warning in result.summary["warnings"])


def test_generate_next_day_prediction_rejects_blocked_gate() -> None:
    signals = _technical_signals()
    blocked_gate = QualityGateResult(
        status="blocked",
        can_predict=False,
        reasons=("预测输入被阻断",),
        warnings=(),
        checks={},
        data_cutoff="2026-04-24",
        checked_at="2026-08-31T00:00:00+00:00",
    )

    with pytest.raises(PredictionInputBlockedError, match="预测输入被阻断"):
        generate_next_day_prediction(replace(signals, quality_gate=blocked_gate))


def test_generate_next_day_prediction_validates_component_score() -> None:
    with pytest.raises(NextDayPredictionError, match="score"):
        generate_next_day_prediction(_technical_signals(), market_environment={"score": 2})

    with pytest.raises(NextDayPredictionError, match="无法估计价格区间"):
        data = _technical_signals().data.drop(columns=["atr_14", "volatility_20"])
        generate_next_day_prediction(replace(_technical_signals(), data=data))
