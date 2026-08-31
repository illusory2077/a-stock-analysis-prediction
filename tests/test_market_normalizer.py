from datetime import date, datetime, timezone

import pandas as pd

from src.market import normalize_daily_bars, validate_daily_bars


def test_normalize_tushare_rows_and_infer_metadata() -> None:
    data = pd.DataFrame(
        [
            {"ts_code": "600519.SH", "trade_date": "20260819", "open": "1,300.50", "high": "1310", "low": "1290", "close": "1305", "vol": "1000", "amount": "1,305,000", "pre_close": 1299},
        ]
    )
    normalized = normalize_daily_bars(
        data,
        source="tushare",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 19),
        retrieved_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    assert list(normalized["symbol"]) == ["600519.SH"]
    assert normalized.iloc[0]["exchange"] == "SSE"
    assert normalized.iloc[0]["asset_type"] == "stock"
    assert normalized.iloc[0]["trade_date"] == date(2026, 8, 19)
    assert normalized.iloc[0]["open"] == 1300.5
    assert normalized.iloc[0]["price_adjustment"] == "none"
    assert normalized.iloc[0]["extra_pre_close"] == 1299


def test_normalizer_rejects_missing_required_row_without_zero_fill() -> None:
    normalized = normalize_daily_bars(
        [{"symbol": "600519.SH", "trade_date": "2026-08-19", "close": "N/A"}],
        source="test",
    )
    assert normalized.empty
    assert len(normalized.attrs["rejected_data"]) == 1
    assert "open" in normalized.attrs["rejected_data"].iloc[0]["_rejection_reason"]


def test_quality_checks_ohlc_duplicates_and_sorts() -> None:
    raw = [
        {"symbol": "600519.SH", "trade_date": "2026-08-20", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "amount": 10},
        {"symbol": "600519.SH", "trade_date": "2026-08-19", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "amount": 10},
        {"symbol": "600519.SH", "trade_date": "2026-08-19", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "amount": 10},
        {"symbol": "600519.SH", "trade_date": "2026-08-18", "open": 10, "high": 8, "low": 9, "close": 10, "volume": 1, "amount": 10},
    ]
    normalized = normalize_daily_bars(raw, source="test")
    quality = validate_daily_bars(
        normalized,
        rejected_data=normalized.attrs["rejected_data"],
        input_rows=normalized.attrs["input_rows"],
        warnings=normalized.attrs["normalization_warnings"],
        column_mapping=normalized.attrs["column_mapping"],
    )
    assert list(quality.data["trade_date"]) == [date(2026, 8, 19), date(2026, 8, 20)]
    assert quality.report["duplicates_removed"] == 1
    assert quality.report["rejected_rows"] == 1
    assert quality.report["status"] == "validated_with_warning"
