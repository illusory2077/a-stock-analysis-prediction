from datetime import date, datetime, timezone

import pandas as pd

from src.analysis import evaluate_news
from src.data_providers import DataProviderError, DataSourceRouter
from src.data_providers.tushare_provider import TushareProvider
from src.market import (
    deduplicate_disclosures,
    filter_disclosures_by_cutoff,
    normalize_disclosures,
    validate_disclosures,
)
from src.storage import DisclosureStore


def test_disclosure_normalization_and_cutoff() -> None:
    data = pd.DataFrame([
        {"ts_code": "600519.SH", "file_name": "业绩增长公告", "ann_date": "2026-08-31 10:00:00", "url": "https://example.test/a", "id": "a1"},
        {"ts_code": "600519.SH", "end_date": "2026-06-30", "ann_date": "2026-09-01 09:00:00", "report_type": "Q2", "n_income": 10, "id": "f1"},
        {"ts_code": "600519.SH", "file_name": "无日期公告", "id": "m1"},
    ])
    normalized = normalize_disclosures(data, symbol="600519.SH", report_type="announcement", source="tushare", retrieved_at=datetime(2026, 8, 31, tzinfo=timezone.utc))
    quality = validate_disclosures(normalized, rejected_data=normalized.attrs["rejected_data"], input_rows=normalized.attrs["input_rows"], warnings=normalized.attrs["normalization_warnings"], column_mapping=normalized.attrs["column_mapping"])
    assert quality.report["status"] == "validated_with_warning"
    assert normalized.iloc[0]["published_at_utc"] == "2026-08-31T02:00:00+00:00"
    result = filter_disclosures_by_cutoff(normalized.to_dict(orient="records"), cutoff=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc))
    assert [item["source_record_id"] for item in result.eligible] == ["a1"]
    assert {item["source_record_id"] for item in result.excluded} == {"m1"}
    assert result.report["counts"]["after_cutoff"] == 0


def test_disclosure_cutoff_uses_publication_not_report_period() -> None:
    result = filter_disclosures_by_cutoff(
        [{"source_record_id": "q2", "report_type": "financial_report", "report_period": date(2026, 6, 30), "published_at": "2026-09-01T09:00:00+08:00"}],
        cutoff=date(2026, 8, 31),
    )
    assert not result.eligible
    assert result.excluded[0]["cutoff_exclusion_reason"] == "after_cutoff"


def test_disclosures_join_news_score_and_deduplicate() -> None:
    items = [{"news_id": "n1", "title": "市场消息", "published_at": "2026-08-31T08:00:00+08:00", "source": "news"}]
    disclosures = [
        {"source_record_id": "a1", "report_type": "announcement", "title": "公司获批重大订单", "published_at": "2026-08-31T09:00:00+08:00", "source": "tushare"},
        {"source_record_id": "a1", "report_type": "announcement", "title": "重复", "published_at": "2026-08-31T09:00:00+08:00", "source": "tushare"},
    ]
    assert len(deduplicate_disclosures(disclosures)) == 1
    score = evaluate_news(items, disclosures=disclosures, cutoff=date(2026, 8, 31), generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert score.score is not None and score.score > 0
    assert score.summary["announcement_eligible_count"] == 1
    assert score.summary["eligible_count"] == 2


def test_router_disclosures_and_store(tmp_path) -> None:
    class Provider:
        name = "fake"
        data_version = "v1"

        def disclosures(self, symbol, start_date, end_date):
            return {"announcements": [{"ts_code": symbol, "title": "获批订单", "ann_date": "2026-08-31 10:00:00", "id": "a1"}], "financial_reports": [{"ts_code": symbol, "end_date": "2026-06-30", "ann_date": "2026-08-30 10:00:00", "n_income": 1, "id": "f1"}]}

    route = DataSourceRouter(market_providers=[Provider()], cross_validate_market=False).fetch_disclosures("600519.SH", date(2026, 8, 1), date(2026, 8, 31))
    assert set(route["data"]["report_type"]) == {"announcement", "financial_report"}
    paths = DisclosureStore(tmp_path).save_batch("600519.SH", route["source"], route["raw_data"], route["data"], rejected_data=route["rejected_data"], quality_report=route["quality_report"])
    assert all(tmp_path.joinpath("raw/disclosures").exists() for _ in [0])
    assert paths["processed_path"].endswith(".jsonl")


def test_financial_report_prefers_actual_announcement_date() -> None:
    normalized = normalize_disclosures(
        [{
            "ts_code": "600519.SH",
            "ann_date": "2026-08-01",
            "f_ann_date": "2026-08-31 10:00:00",
            "end_date": "2025-12-31",
            "n_income": 1,
            "id": "f1",
        }],
        symbol="600519.SH",
        report_type="financial_report",
    )
    assert normalized.iloc[0]["published_at"] == "2026-08-31T02:00:00+00:00"
    assert normalized.iloc[0]["report_period"].isoformat() == "2025-12-31"


def test_router_disclosures_falls_back_to_next_provider() -> None:
    class FailingProvider:
        name = "primary"
        data_version = "v1"

        def disclosures(self, symbol, start_date, end_date):
            raise DataProviderError("primary unavailable")

    class BackupProvider:
        name = "backup"
        data_version = "v2"

        def disclosures(self, symbol, start_date, end_date):
            return {
                "announcements": [{
                    "ts_code": symbol, "title": "获批订单",
                    "ann_date": "2026-08-31 10:00:00", "id": "a1",
                }],
                "financial_reports": [],
            }

    route = DataSourceRouter(
        market_providers=[FailingProvider(), BackupProvider()],
        cross_validate_market=False,
    ).fetch_disclosures("600519.SH", date(2026, 8, 1), date(2026, 8, 31))

    assert route["source"] == "backup"
    assert route["degraded"] is True
    assert route["attempts"][0]["ok"] is False
    assert route["attempts"][1]["ok"] is True


def test_tushare_disclosures_queries_full_income_history_for_publication_filter() -> None:
    calls: list[tuple[str, dict]] = []

    class Client:
        def anns_d(self, **kwargs):
            calls.append(("anns_d", kwargs))
            return pd.DataFrame([{
                "ts_code": "600519.SH", "ann_date": "2026-08-31", "title": "公告", "id": "a1",
            }])

        def income(self, **kwargs):
            calls.append(("income", kwargs))
            return pd.DataFrame([{
                "ts_code": "600519.SH", "ann_date": "2026-08-01",
                "f_ann_date": "2026-08-31", "end_date": "2025-12-31",
                "n_income": 1,
            }])

    provider = TushareProvider(token="test-token")
    provider._client = lambda: Client()
    result = provider.disclosures("600519.SH", date(2026, 8, 1), date(2026, 8, 31))

    assert set(result) == {"announcements", "financial_reports"}
    assert calls[0][0] == "anns_d"
    assert calls[0][1]["start_date"] == "20260801"
    assert calls[0][1]["end_date"] == "20260831"
    income_kwargs = calls[1][1]
    assert "start_date" not in income_kwargs and "end_date" not in income_kwargs
    assert "f_ann_date" in income_kwargs["fields"]

