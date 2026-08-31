from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

import scripts.run_daily_pipeline as pipeline
from src.data_providers import DataProviderError


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
            "source": "fake",
            "retrieved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )


def _quality() -> dict:
    return {
        "status": "validated",
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "data_range": {"start_date": "2026-01-05", "end_date": "2026-04-10"},
        "calendar_validation": {"status": "validated"},
        "cross_validation": {"status": "matched"},
    }


def _route(data: pd.DataFrame) -> dict:
    return {
        "data": data,
        "quality_report": _quality(),
        "raw_data": {"fake": True},
        "rejected_data": None,
        "retrieved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        "source": "fake",
        "data_version": "test-v1",
        "degraded": False,
        "attempts": [{"provider": "fake", "ok": True}],
        "secondary_audits": [],
    }


class _FakeDisclosureStore:
    saved = 0

    def __init__(self, *args, **kwargs):
        pass

    def save_batch(self, *args, **kwargs):
        type(self).saved += 1
        return {
            "raw_path": "raw/disclosures/fake/2026-08-31/raw.json",
            "processed_path": "processed/disclosures/600519.SH_2026-08-31.jsonl",
            "quality_path": "processed/disclosures/600519.SH_2026-08-31_quality.json",
        }


class _FakeRouter:
    disclosure_should_fail = False

    def __init__(self, *args, **kwargs):
        pass

    def fetch_index_bars(self, symbol, start_date, end_date):
        return _route(_bars())

    def fetch_daily_bars(self, symbol, start_date, end_date):
        return _route(_bars())

    def search_news(self, *args, **kwargs):
        raise DataProviderError("test news disabled")

    def fetch_disclosures(self, symbol, start_date, end_date):
        if self.disclosure_should_fail:
            raise DataProviderError("test disclosures unavailable")
        disclosures = pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "report_type": "announcement",
                    "title": "公司获批重大订单",
                    "published_at": "2026-04-10T02:00:00+00:00",
                    "published_at_utc": "2026-04-10T02:00:00+00:00",
                    "source": "fake",
                    "source_record_id": "a1",
                },
                {
                    "symbol": symbol,
                    "report_type": "financial_report",
                    "title": "年度业绩增长",
                    "report_period": "2025-12-31",
                    "published_at": "2026-04-10T03:00:00+00:00",
                    "published_at_utc": "2026-04-10T03:00:00+00:00",
                    "source": "fake",
                    "source_record_id": "f1",
                }
            ]
        )
        route = _route(disclosures)
        route["quality_report"] = {
            "status": "validated",
            "input_rows": 2,
            "output_rows": 2,
            "rejected_rows": 0,
            "warnings": [],
            "errors": [],
        }
        return route

    def fetch_fund_flow(self, *args, **kwargs):
        raise DataProviderError("test fund flow disabled")

    def fetch_margin(self, *args, **kwargs):
        raise DataProviderError("test margin disabled")

    def fetch_dragon_tiger(self, *args, **kwargs):
        raise DataProviderError("test dragon tiger disabled")


def _patch_savers(monkeypatch, tmp_path: Path) -> None:
    def paths(*args, **kwargs):
        return {
            "data_path": str(tmp_path / "data.json"),
            "quality_path": str(tmp_path / "quality.json"),
            "prediction_path": str(tmp_path / "prediction.json"),
            "metadata_path": str(tmp_path / "metadata.json"),
        }

    for name in (
        "save_market_data",
        "save_fund_flow",
        "save_margin",
        "save_dragon_tiger",
        "save_secondary_margin_audit",
        "save_secondary_dragon_tiger_audit",
        "save_secondary_fund_flow_audit",
        "save_secondary_market_audit",
        "save_technical_indicators",
        "save_technical_signals",
        "save_next_day_prediction",
    ):
        monkeypatch.setattr(pipeline, name, paths)
    monkeypatch.setattr(pipeline, "DisclosureStore", _FakeDisclosureStore)


def _run_pipeline(monkeypatch, report_path: Path) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_daily_pipeline.py",
            "--symbol",
            "600519.SH",
            "--date",
            "2026-04-10",
            "--lookback-days",
            "0",
            "--report",
            str(report_path),
        ],
    )
    return pipeline.main()


def test_main_end_to_end_includes_disclosures_in_report(monkeypatch, tmp_path: Path) -> None:
    _FakeDisclosureStore.saved = 0
    _FakeRouter.disclosure_should_fail = False
    _patch_savers(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "DataSourceRouter", _FakeRouter)
    report_path = tmp_path / "daily.md"

    exit_code = _run_pipeline(monkeypatch, report_path)

    assert exit_code == 0
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "#### 公告与财报" in report
    assert "有效公告：`1`" in report
    assert "有效财报：`1`" in report
    assert "公告/财报消息面贡献：" in report
    assert "标准化数据：`processed/disclosures/600519.SH_2026-08-31.jsonl`" in report
    assert _FakeDisclosureStore.saved == 1


def test_disclosure_failure_does_not_block_market_report(monkeypatch, tmp_path: Path) -> None:
    _FakeDisclosureStore.saved = 0
    _FakeRouter.disclosure_should_fail = True
    _patch_savers(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "DataSourceRouter", _FakeRouter)
    report_path = tmp_path / "daily-disclosure-failure.md"

    exit_code = _run_pipeline(monkeypatch, report_path)

    assert exit_code == 0
    report = report_path.read_text(encoding="utf-8")
    assert "## 行情数据" in report
    assert "600519.SH" in report
    assert "公告/财报获取警告：test disclosures unavailable" in report
    assert _FakeDisclosureStore.saved == 0

