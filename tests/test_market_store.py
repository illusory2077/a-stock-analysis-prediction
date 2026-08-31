import json
from datetime import date, datetime, timezone

import pandas as pd

from src.storage import save_market_data, save_secondary_market_audit, save_technical_indicators, save_technical_signals


def test_save_market_data_writes_raw_quality_and_rejected(tmp_path) -> None:
    data = pd.DataFrame(
        [{"symbol": "600519.SH", "trade_date": date(2026, 8, 19), "open": 1, "high": 2, "low": 1, "close": 2}]
    )
    rejected = pd.DataFrame([{"symbol": "bad", "_rejection_reason": "invalid"}])
    paths = save_market_data(
        "600519.SH",
        data,
        source="test",
        trade_date=date(2026, 8, 19),
        root=tmp_path,
        raw_data=[{"raw": "value"}],
        rejected_data=rejected,
        quality_report={"status": "validated_with_warning", "rejected_rows": 1},
    )
    assert tmp_path.joinpath("raw/market/test/2026-08-19/600519.SH_raw.json").exists()
    assert tmp_path.joinpath("processed/market/600519.SH_2026-08-19_quality.json").exists()
    assert tmp_path.joinpath("processed/market/rejected/600519.SH_2026-08-19_rejected.jsonl").exists()
    assert paths["data_path"].endswith(".parquet")

    second = save_market_data(
        "600519.SH",
        data,
        source="test",
        trade_date=date(2026, 8, 19),
        root=tmp_path,
        raw_data=[{"raw": "second"}],
    )
    assert second["raw_path"] != paths["raw_path"]


def test_save_secondary_market_audit_writes_wrapped_raw_and_comparison(tmp_path) -> None:
    retrieved_at = datetime(2026, 8, 19, 8, 30, tzinfo=timezone.utc)
    comparison = {
        "status": "mismatch",
        "secondary_source": "akshare",
        "metrics": {"close": {"max_diff_pct": 2.0, "threshold_pct": 0.5}},
    }
    paths = save_secondary_market_audit(
        "600519.SH",
        "akshare",
        date(2026, 8, 19),
        [{"日期": "2026-08-19", "收盘": 100}],
        comparison,
        quality_report={"status": "validated"},
        retrieved_at=retrieved_at,
        data_version="akshare-test",
        root=tmp_path,
    )

    raw_path = tmp_path / "raw/market/akshare/2026-08-19/600519.SH_secondary_raw.json"
    audit_path = tmp_path / "processed/market/audit/600519.SH_2026-08-19_akshare_cross_validation.json"
    assert paths["raw_path"] == str(raw_path)
    assert paths["audit_path"] == str(audit_path)
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert raw_payload["source"] == "akshare"
    assert raw_payload["retrieved_at"] == retrieved_at.isoformat()
    assert raw_payload["raw_data"][0]["收盘"] == 100
    assert audit_payload["status"] == "mismatch"
    assert audit_payload["raw_path"] == str(raw_path)
    assert audit_payload["comparison"]["metrics"]["close"]["max_diff_pct"] == 2.0

    second = save_secondary_market_audit(
        "600519.SH",
        "akshare",
        date(2026, 8, 19),
        [{"raw": "second"}],
        {"status": "matched"},
        retrieved_at=retrieved_at,
        root=tmp_path,
    )
    assert second["raw_path"] != paths["raw_path"]
    assert second["audit_path"] != paths["audit_path"]


def test_save_secondary_market_audit_records_unavailable_without_raw(tmp_path) -> None:
    paths = save_secondary_market_audit(
        "600519.SH",
        "akshare",
        date(2026, 8, 19),
        comparison_report={"status": "unavailable", "warnings": ["接口失败"]},
        error="接口失败",
        root=tmp_path,
    )
    assert "raw_path" not in paths
    payload = json.loads((tmp_path / "processed/market/audit/600519.SH_2026-08-19_akshare_cross_validation.json").read_text(encoding="utf-8"))
    assert payload["status"] == "unavailable"
    assert payload["error"] == "接口失败"


def test_save_technical_indicators_writes_data_and_metadata(tmp_path) -> None:
    data = pd.DataFrame(
        [{"symbol": "600519.SH", "trade_date": date(2026, 8, 19), "close": 100.0, "ma_5": 99.0}]
    )
    paths = save_technical_indicators(
        "600519.SH",
        data,
        source="tushare",
        trade_date=date(2026, 8, 19),
        indicator_summary={"history_rows": 1, "warnings": []},
        quality_gate={"status": "approved", "can_predict": True},
        data_version="test-v1",
        root=tmp_path,
    )

    assert paths["data_path"].endswith(".parquet")
    assert (tmp_path / "processed/market/indicators/600519.SH_2026-08-19_technical_indicators.parquet").exists()
    metadata = json.loads((tmp_path / "processed/market/indicators/600519.SH_2026-08-19_technical_indicators_metadata.json").read_text(encoding="utf-8"))
    assert metadata["source"] == "tushare"
    assert metadata["data_version"] == "test-v1"
    assert metadata["quality_gate"]["status"] == "approved"


def test_save_technical_signals_writes_data_and_metadata(tmp_path) -> None:
    data = pd.DataFrame(
        [{"symbol": "600519.SH", "trade_date": date(2026, 8, 19), "close": 100.0, "signal_direction": "bullish"}]
    )
    paths = save_technical_signals(
        "600519.SH",
        data,
        source="tushare",
        trade_date=date(2026, 8, 19),
        signal_summary={"direction": "bullish", "confidence": 0.8},
        quality_gate={"status": "approved", "can_predict": True},
        data_version="test-v1",
        root=tmp_path,
    )

    assert paths["data_path"].endswith(".parquet")
    assert (tmp_path / "processed/market/signals/600519.SH_2026-08-19_technical_signals.parquet").exists()
    metadata = json.loads((tmp_path / "processed/market/signals/600519.SH_2026-08-19_technical_signals_metadata.json").read_text(encoding="utf-8"))
    assert metadata["source"] == "tushare"
    assert metadata["signal_summary"]["direction"] == "bullish"
    assert metadata["quality_gate"]["status"] == "approved"
