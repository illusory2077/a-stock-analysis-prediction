from __future__ import annotations

from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.run_daily_pipeline import _render_report, build_parser
from src.analysis import calculate_technical_indicators


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


def test_render_report_includes_latest_technical_indicators(tmp_path: Path) -> None:
    result = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")
    args = Namespace(symbol=["600519.SH"])
    route = {
        "source": "test",
        "degraded": False,
        "data": result.data,
        "quality_report": _report(),
        "prediction_quality_gate": result.quality_gate.to_dict(),
        "technical_indicators": result,
        "technical_indicator_paths": {"data_path": "indicators.parquet"},
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
