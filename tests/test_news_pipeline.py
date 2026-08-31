from datetime import date, datetime, timezone

from src.news import (
    NewsStore,
    deduplicate_news,
    filter_news_by_cutoff,
    normalize_tavily_response,
)
from src.analysis import evaluate_news


def test_normalize_and_deduplicate_tavily_results(tmp_path) -> None:
    response = {
        "results": [
            {
                "title": "市场消息",
                "url": "https://example.com/news?id=1&utm_source=tavily",
                "content": "摘要",
                "published_date": "2026-08-19T01:00:00Z",
                "score": 0.9,
            },
            {
                "title": "市场消息转载",
                "url": "https://example.com/news?id=1&utm_medium=newsletter",
                "content": "同一来源",
                "score": 0.8,
            },
        ]
    }
    items = normalize_tavily_response(
        response,
        query="市场",
        retrieved_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        related_symbols=["600519.SH"],
    )
    assert items[0]["source_provider"] == "tavily"
    assert items[0]["related_symbols"] == ["600519.SH"]
    assert len(deduplicate_news(items)) == 1

    paths = NewsStore(tmp_path).save_batch("tavily", response, items)
    assert tmp_path.joinpath("raw/news/tavily").exists()
    assert tmp_path.joinpath("processed/news").exists()
    assert paths["raw_path"].endswith(".json")
    assert paths["processed_path"].endswith(".jsonl")


def test_news_cutoff_excludes_future_and_undated_items() -> None:
    items = [
        {"news_id": "before", "title": "公司业绩增长，获批新订单", "published_at": "2026-08-31T08:00:00+08:00"},
        {"news_id": "after", "title": "公司被处罚", "published_at": "2026-09-01T00:01:00+08:00"},
        {"news_id": "missing", "title": "市场消息"},
        {"news_id": "invalid", "title": "市场消息", "published_at": "not-a-date"},
    ]

    result = filter_news_by_cutoff(items, cutoff=datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc))

    assert [item["news_id"] for item in result.eligible] == ["before"]
    assert {item["news_id"] for item in result.excluded} == {"after", "missing", "invalid"}
    assert result.report["counts"] == {
        "eligible": 1,
        "missing_published_at": 1,
        "invalid_published_at": 1,
        "after_cutoff": 1,
    }
    assert any("未来函数" in warning for warning in result.report["warnings"])


def test_news_score_uses_only_pre_cutoff_published_items() -> None:
    result = evaluate_news(
        [
            {
                "news_id": "positive",
                "title": "公司业绩增长，获批重大订单",
                "published_at": "2026-08-31T10:00:00+08:00",
                "source": "example.com",
                "score": 0.9,
            },
            {
                "news_id": "future-negative",
                "title": "公司遭处罚，业绩下滑",
                "published_at": "2026-09-01T09:00:00+08:00",
                "source": "example.com",
                "score": 0.9,
            },
        ],
        cutoff=date(2026, 8, 31),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    assert result.score is not None and result.score > 0
    assert result.data_cutoff == "2026-08-31"
    assert result.summary["eligible_count"] == 1
    assert result.summary["excluded_count"] == 1
    assert result.to_dimension()["available"] is True


def test_normalize_news_preserves_utc_publication_time() -> None:
    items = normalize_tavily_response(
        {"results": [{"title": "市场消息", "url": "https://example.com/a", "published_date": "2026-08-19T01:00:00Z"}]},
        query="市场",
        retrieved_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert items[0]["published_at_utc"] == "2026-08-19T01:00:00+00:00"
