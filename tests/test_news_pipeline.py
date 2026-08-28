from datetime import datetime, timezone

from src.news import NewsStore, deduplicate_news, normalize_tavily_response


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
