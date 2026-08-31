from datetime import date, datetime, timezone

import pandas as pd

from src.analysis import PredictionInputBlockedError, evaluate_prediction_input, require_prediction_input


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "exchange": "SSE",
                "asset_type": "stock",
                "trade_date": date(2026, 8, 19),
                "open": 99,
                "high": 101,
                "low": 98,
                "close": 100,
                "frequency": "1d",
                "price_adjustment": "none",
                "source": "tushare",
                "retrieved_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
            }
        ]
    )


def _report(*, cross_status: str = "matched", quality_status: str = "validated", **kwargs):
    return {
        "status": quality_status,
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "calendar_validation": {"status": "validated", "unexpected_non_trading_dates": []},
        "cross_validation": {"status": cross_status},
        **kwargs,
    }


def test_quality_gate_approved_when_primary_and_secondary_checks_are_clean() -> None:
    result = evaluate_prediction_input(
        _bars(),
        _report(),
        symbol="600519.SH",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
    )

    assert result.status == "approved"
    assert result.can_predict is True
    assert result.reasons == ()
    assert result.data_cutoff == "2026-08-19"


def test_quality_gate_degraded_when_secondary_validation_is_unavailable() -> None:
    result = evaluate_prediction_input(
        _bars(),
        _report(cross_status="unavailable"),
        symbol="600519.SH",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
    )

    assert result.status == "degraded"
    assert result.can_predict is True
    assert any("交叉验证状态为 unavailable" in item for item in result.warnings)


def test_quality_gate_degraded_when_router_falls_back_or_quality_has_warnings() -> None:
    result = evaluate_prediction_input(
        _bars(),
        _report(quality_status="validated_with_warning", rejected_rows=1),
        symbol="600519.SH",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
        route_degraded=True,
    )

    assert result.status == "degraded"
    assert result.can_predict is True
    assert any("备用主源" in item for item in result.warnings)
    assert any("拒绝 1 条记录" in item for item in result.warnings)


def test_quality_gate_blocks_cross_validation_mismatch() -> None:
    result = evaluate_prediction_input(
        _bars(),
        _report(cross_status="mismatch"),
        symbol="600519.SH",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
    )

    assert result.status == "blocked"
    assert result.can_predict is False
    assert any("mismatch" in item for item in result.reasons)


def test_quality_gate_blocks_primary_quality_errors_and_non_trading_rows() -> None:
    result = evaluate_prediction_input(
        _bars(),
        _report(
            quality_status="validated_with_warning",
            errors=["high 小于 open/close 的最大值"],
            calendar_validation={
                "status": "validated_with_warning",
                "unexpected_non_trading_dates": ["2026-08-19"],
            },
        ),
        symbol="600519.SH",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
    )

    assert result.status == "blocked"
    assert result.can_predict is False
    assert any("主源质量错误" in item for item in result.reasons)
    assert any("非交易日行情" in item for item in result.reasons)


def test_quality_gate_blocks_empty_or_missing_quality_report() -> None:
    result = evaluate_prediction_input(pd.DataFrame(), None, symbol="600519.SH")

    assert result.status == "blocked"
    assert result.can_predict is False
    assert "预测输入行情为空" in result.reasons
    assert "缺少主源质量报告" in result.reasons


def test_quality_gate_blocks_unchecked_quality_report_or_invalid_dates() -> None:
    bars = _bars()
    bars.loc[0, "trade_date"] = "not-a-date"
    result = evaluate_prediction_input(
        bars,
        {"status": "not_checked", "errors": [], "warnings": []},
        symbol="600519.SH",
    )

    assert result.status == "blocked"
    assert result.can_predict is False
    assert any("未通过" in item for item in result.reasons)
    assert any("无效交易日期" in item for item in result.reasons)


def test_require_prediction_input_raises_for_blocked_data() -> None:
    try:
        require_prediction_input(pd.DataFrame(), None)
    except PredictionInputBlockedError as exc:
        assert exc.result.status == "blocked"
    else:
        raise AssertionError("blocked 输入未抛出 PredictionInputBlockedError")


def test_quality_gate_blocks_multiple_symbols_for_single_prediction() -> None:
    bars = pd.concat([_bars(), _bars().assign(symbol="000001.SZ")], ignore_index=True)
    result = evaluate_prediction_input(
        bars,
        _report(),
    )

    assert result.status == "blocked"
    assert any("多个标的" in item for item in result.reasons)
