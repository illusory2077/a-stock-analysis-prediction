from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_providers import DataProviderError, DataSourceRouter  # noqa: E402
from src.news import NewsStore, deduplicate_news, normalize_tavily_response  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="搜索并保存市场新闻")
    parser.add_argument("query", help="搜索关键词，例如：贵州茅台 或 A股市场")
    parser.add_argument("--time-range", choices=("day", "week", "month", "year"), default="week")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--symbols", default="", help="逗号分隔的关联标的代码")
    parser.add_argument("--include-raw-content", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    retrieved_at = datetime.now(timezone.utc)
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]

    try:
        routed = DataSourceRouter().search_news(
            args.query,
            time_range=args.time_range,
            max_results=args.max_results,
            include_raw_content=args.include_raw_content,
        )
    except (DataProviderError, ValueError) as exc:
        print(f"新闻搜索失败: {exc}", file=sys.stderr)
        return 1

    response = routed["data"]
    items = deduplicate_news(
        normalize_tavily_response(response, query=args.query, retrieved_at=retrieved_at, related_symbols=symbols)
    )
    paths = NewsStore().save_batch("tavily", response, items, retrieved_at=retrieved_at)
    print(json.dumps({"source": routed["source"], "degraded": routed["degraded"], "count": len(items), **paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
