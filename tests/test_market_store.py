from datetime import date

import pandas as pd

from src.storage import save_market_data


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
