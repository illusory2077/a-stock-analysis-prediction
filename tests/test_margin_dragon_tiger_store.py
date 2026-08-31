from datetime import date, datetime, timezone

import pandas as pd

from src.storage import save_dragon_tiger, save_margin


def test_save_margin_and_dragon_tiger_write_audit_files(tmp_path) -> None:
    margin = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "margin_balance": [100.0]})
    dragon = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "net_buy_amount": [100.0]})
    margin_paths = save_margin("600519.SH", margin, source="tushare", trade_date=date(2026, 8, 19), raw_data=[{"rzye": 1}], retrieved_at=datetime(2026, 8, 20, tzinfo=timezone.utc), root=tmp_path)
    dragon_paths = save_dragon_tiger("600519.SH", dragon, source="akshare", trade_date=date(2026, 8, 19), raw_data=[{"净买入额": 100}], retrieved_at=datetime(2026, 8, 20, tzinfo=timezone.utc), root=tmp_path)
    assert (tmp_path / "raw/margin/tushare/2026-08-19/600519.SH_raw.json").exists()
    assert (tmp_path / "processed/margin/600519.SH_2026-08-19_quality.json").exists()
    assert (tmp_path / "raw/dragon_tiger/akshare/2026-08-19/600519.SH_raw.json").exists()
    assert margin_paths["metadata_path"] and dragon_paths["metadata_path"]
