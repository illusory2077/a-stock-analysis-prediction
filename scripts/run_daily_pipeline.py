from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_providers import DataProviderError, DataSourceRouter  # noqa: E402
from src.news import NewsStore, deduplicate_news, normalize_tavily_response  # noqa: E402
from src.storage import save_market_data  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行每日行情、新闻和报告流水线")
    parser.add_argument("--symbol", nargs="+", required=True, help="标的代码，例如 600519.SH")
    parser.add_argument("--date", default=date.today().isoformat(), help="交易日期，默认今天，格式 YYYY-MM-DD")
    parser.add_argument("--query", default="A股市场 财经新闻", help="新闻搜索关键词")
    parser.add_argument("--news-time-range", choices=("day", "week", "month", "year"), default="day")
    parser.add_argument("--max-news", type=int, default=10)
    parser.add_argument("--report", default="", help="报告输出路径，默认 reports/daily_YYYY-MM-DD.md")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    trade_date = date.fromisoformat(args.date)
    retrieved_at = datetime.now(timezone.utc)
    router = DataSourceRouter()
    market_results: list[dict[str, Any]] = []
    failures: list[str] = []

    for symbol in args.symbol:
        try:
            routed = router.fetch_daily_bars(symbol, trade_date, trade_date)
            paths = save_market_data(
                symbol,
                routed["data"],
                source=routed["source"],
                trade_date=trade_date,
                raw_data=routed.get("raw_data"),
                rejected_data=routed.get("rejected_data"),
                quality_report=routed.get("quality_report"),
                retrieved_at=routed.get("retrieved_at", retrieved_at),
            )
            market_results.append({"symbol": symbol, "route": routed, "paths": paths})
        except (DataProviderError, ValueError) as exc:
            failures.append(f"{symbol}: {exc}")

    news_result: dict[str, Any] | None = None
    news_error = ""
    try:
        query = f"{args.query} {' '.join(args.symbol)}".strip()
        routed_news = router.search_news(query, time_range=args.news_time_range, max_results=args.max_news)
        raw_response = routed_news["data"]
        items = deduplicate_news(normalize_tavily_response(raw_response, query=query, retrieved_at=retrieved_at, related_symbols=args.symbol))
        paths = NewsStore().save_batch("tavily", raw_response, items, retrieved_at=retrieved_at)
        news_result = {"route": routed_news, "items": items, "paths": paths}
    except (DataProviderError, ValueError) as exc:
        news_error = str(exc)

    report_path = Path(args.report) if args.report else ROOT / "reports" / f"daily_{trade_date.isoformat()}.md"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(args, trade_date, retrieved_at, market_results, failures, news_result, news_error, report_path), encoding="utf-8")
    print(f"报告已生成: {report_path}")
    print(f"Markdown绝对路径链接: [{report_path}]({report_path.as_uri()})")
    print(f"行情成功: {len(market_results)}，行情失败: {len(failures)}，新闻: {len(news_result['items']) if news_result else 0}")
    return 0 if market_results or news_result else 1


def _render_report(
    args: argparse.Namespace,
    trade_date: date,
    retrieved_at: datetime,
    market_results: list[dict[str, Any]],
    failures: list[str],
    news_result: dict[str, Any] | None,
    news_error: str,
    report_path: Path,
) -> str:
    lines = [
        f"# 每日市场采集报告 - {trade_date.isoformat()}",
        "",
        f"- **报告文件绝对路径/Markdown链接**：[{report_path}]({report_path.as_uri()})",
        "",
        f"- 采集时间（UTC）：{retrieved_at.isoformat()}",
        f"- 标的：{', '.join(args.symbol)}",
        f"- 数据截止日：{trade_date.isoformat()}",
        "",
        "## 行情数据",
        "",
    ]
    for result in market_results:
        route = result["route"]
        data = route["data"]
        quality = route.get("quality_report", {})
        lines.append(f"### {result['symbol']}")
        lines.append(f"- 来源：`{route['source']}`；是否降级：`{route['degraded']}`")
        lines.append(f"- 质量状态：`{quality.get('status', 'unknown')}`；规则版本：`{quality.get('quality_rules_version', 'unknown')}`")
        lines.append(f"- 记录数：{len(data) if hasattr(data, '__len__') else '未知'}；拒绝数：`{quality.get('rejected_rows', 0)}`；去重数：`{quality.get('duplicates_removed', 0)}`")
        calendar_validation = quality.get("calendar_validation", {})
        lines.append(
            f"- 交易日历校验：`{calendar_validation.get('status', 'unknown')}`"
            + (f"；交易所：`{calendar_validation.get('exchange')}`" if calendar_validation.get("exchange") else "")
        )
        cross_validation = quality.get("cross_validation", {})
        lines.append(f"- 主备交叉验证：`{cross_validation.get('status', 'unknown')}`")
        for detail in cross_validation.get("details", []):
            metrics = detail.get("metrics", {})
            metric_text = ", ".join(
                f"{field}最大差异 {values.get('max_diff_pct'):.4f}% / 阈值 {values.get('threshold_pct'):.4f}%"
                for field, values in metrics.items()
                if values.get("max_diff_pct") is not None
            )
            if metric_text:
                lines.append(f"  - 备用源 `{detail.get('secondary_source', 'unknown')}`：{metric_text}")
        lines.append(f"- 保存文件：`{result['paths']['data_path']}`")
        if result["paths"].get("raw_path"):
            lines.append(f"- 原始数据：`{result['paths']['raw_path']}`")
        lines.append(f"- 质量报告：`{result['paths']['quality_path']}`")
        if result["paths"].get("rejected_path"):
            lines.append(f"- 异常行：`{result['paths']['rejected_path']}`")
        if hasattr(data, "columns"):
            lines.append(f"- 字段：`{', '.join(map(str, data.columns))}`")
            if not data.empty:
                lines.append(f"- 最新记录：`{data.iloc[-1].to_dict()}`")
        for warning in quality.get("warnings", []):
            lines.append(f"- 质量警告：{warning}")
        for error in quality.get("errors", []):
            lines.append(f"- 质量错误：{error}")
        lines.append("")
    if failures:
        lines.extend(["### 行情失败或降级详情", "", *[f"- {item}" for item in failures], ""])

    lines.extend(["## 新闻数据", ""])
    if news_result:
        route = news_result["route"]
        lines.append(f"- 来源：`{route['source']}`；是否降级：`{route['degraded']}`")
        lines.append(f"- 结果数：{len(news_result['items'])}")
        lines.append(f"- 原始数据：`{news_result['paths']['raw_path']}`")
        lines.append(f"- 标准化数据：`{news_result['paths']['processed_path']}`")
        lines.append("")
        for item in news_result["items"]:
            title = item["title"] or item["url"]
            lines.append(f"- [{title}]({item['url']})（发布时间：{item.get('published_at') or '未知'}）")
    else:
        lines.append(f"- 新闻搜索失败：{news_error or '未返回结果'}")
    lines.extend(["", "## 数据限制", "", "- 行情统一为未复权日线；实时快照、分钟线和 Tick 不在本轮处理范围。", "- Tavily 用于新闻搜索，不代表实时行情。", "- 本报告仅记录采集结果，不构成投资建议。", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
