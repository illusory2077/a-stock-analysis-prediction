from __future__ import annotations

from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.run_daily_pipeline import _render_report, build_parser
from src.analysis import (
    calculate_technical_indicators,
    evaluate_fund_flow,
    evaluate_market_environment,
    generate_next_day_prediction,
    generate_technical_signals,
)


def _bars() -> pd.DataFrame:
    dates = pd.date_range("2026-01-05", periods=70, freq="B")
    close = pd.Series(range(100, 170), dtype=float)
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


def _report() -> dict:
    return {
        "status": "validated",
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "data_range": {"start_date": "2026-01-05", "end_date": "2026-04-10"},
        "calendar_validation": {"status": "validated"},
        "cross_validation": {"status": "matched"},
    }


def test_parser_exposes_indicator_lookback() -> None:
    args = build_parser().parse_args(["--symbol", "600519.SH"])
    assert args.lookback_days == 120


def _fund_flow() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": "600519.SH",
            "exchange": "SSE",
            "asset_type": "stock",
            "trade_date": pd.date_range("2026-04-06", periods=5, freq="B").date,
            "net_flow_amount": [10000, 15000, 20000, 30000, 50000],
            "currency": "CNY",
            "source": "test-fund-flow",
        }
    )


def test_render_report_includes_latest_technical_indicators(tmp_path: Path) -> None:
    result = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")
    args = Namespace(symbol=["600519.SH"])
    signals = generate_technical_signals(result)
    environment = evaluate_market_environment({"000001.SH": _bars()})
    fund_flow = evaluate_fund_flow(_fund_flow(), generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc))
    prediction = generate_next_day_prediction(
        signals,
        market_environment=environment.to_dimension(),
        fund_flow=fund_flow.to_dimension(),
        symbol="600519.SH",
        target_trade_date=date(2026, 4, 13),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    route = {
        "source": "test",
        "degraded": False,
        "data": result.data,
        "quality_report": _report(),
        "prediction_quality_gate": result.quality_gate.to_dict(),
        "technical_indicators": result,
        "technical_indicator_paths": {"data_path": "indicators.parquet"},
        "technical_signals": signals,
        "technical_signal_paths": {"data_path": "signals.parquet", "metadata_path": "signals_metadata.json"},
        "market_environment": environment,
        "market_environment_paths": {
            "000001.SH": {"data_path": "market/000001.SH.parquet"},
        },
        "fund_flow": fund_flow,
        "fund_flow_paths": {
            "data_path": "fund_flow/600519.SH.parquet",
            "quality_path": "fund_flow/600519.SH_quality.json",
            "secondary_audits": [
                {"audit_path": "fund_flow/audit/600519.SH_akshare.json"},
            ],
        },
        "next_day_prediction": prediction,
        "next_day_prediction_paths": {
            "prediction_path": "predictions/600519.SH_2026-04-10_next_day_prediction.json",
        },
    }
    report = _render_report(
        args,
        date(2026, 4, 10),
        datetime(2026, 8, 31, tzinfo=timezone.utc),
        [{"symbol": "600519.SH", "route": route, "paths": {"data_path": "bars.parquet", "quality_path": "quality.json"}}],
        [],
        None,
        "",
        tmp_path / "daily.md",
    )

    assert "#### 技术指标" in report
    assert "ma_5=" in report
    assert "indicators.parquet" in report
    assert "#### 技术信号" in report
    assert "综合方向：`bullish`" in report
    assert "signals.parquet" in report
    assert "#### 大盘环境" in report
    assert "环境评分：`" in report
    assert "上证指数" in report
    assert "market/000001.SH.parquet" in report
    assert "#### 资金行为" in report
    assert "资金行为评分：`" in report
    assert "最新主力净流入：`+5.00 万元`" in report
    assert "fund_flow/600519.SH.parquet" in report
    assert "fund_flow/600519.SH_quality.json" in report
    assert "fund_flow/audit/600519.SH_akshare.json" in report
    assert "#### 次日预测" in report
    assert "方向概率：看多" in report
    assert "fund_flow" in report
    assert "价格区间：`" in report
    assert "置信度：`" in report
    assert "预测维度未接入真实数据" in report
    assert "600519.SH_2026-04-10_next_day_prediction.json" in report
