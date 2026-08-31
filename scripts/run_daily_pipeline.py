from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import settings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis import (  # noqa: E402
    NextDayPredictionError,
    PredictionInputBlockedError,
    TechnicalIndicatorError,
    TechnicalSignalError,
    calculate_technical_indicators,
    evaluate_fund_flow,
    evaluate_news,
    evaluate_market_environment,
    generate_next_day_prediction,
    generate_technical_signals,
    evaluate_prediction_input,
)
from src.data_providers import DataProviderError, DataSourceRouter  # noqa: E402
from src.news import (  # noqa: E402
    NewsStore,
    deduplicate_news,
    filter_news_by_cutoff,
    normalize_tavily_response,
)
from src.storage import (  # noqa: E402
    save_fund_flow,
    save_market_data,
    save_next_day_prediction,
    save_secondary_fund_flow_audit,
    save_secondary_market_audit,
    save_technical_indicators,
    save_technical_signals,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行每日行情、新闻和报告流水线")
    parser.add_argument("--symbol", nargs="+", required=True, help="标的代码，例如 600519.SH")
    parser.add_argument("--date", default=date.today().isoformat(), help="交易日期，默认今天，格式 YYYY-MM-DD")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="技术指标历史回看天数，默认 120 个自然日；设为 0 仅获取目标日",
    )
    parser.add_argument("--query", default="A股市场 财经新闻", help="新闻搜索关键词")
    parser.add_argument("--news-time-range", choices=("day", "week", "month", "year"), default="day")
    parser.add_argument("--max-news", type=int, default=10)
    parser.add_argument("--report", default="", help="报告输出路径，默认 reports/daily_YYYY-MM-DD.md")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    trade_date = date.fromisoformat(args.date)
    if args.lookback_days < 0:
        raise ValueError("--lookback-days 必须是非负整数")
    history_start = trade_date - timedelta(days=args.lookback_days)
    retrieved_at = datetime.now(timezone.utc)
    router = DataSourceRouter()
    market_results: list[dict[str, Any]] = []
    failures: list[str] = []

    index_symbols = ("000001.SH", "000300.SH", "399006.SZ")
    index_routes: dict[str, dict[str, Any]] = {}
    index_failures: list[str] = []
    for index_symbol in index_symbols:
        try:
            index_routes[index_symbol] = router.fetch_index_bars(index_symbol, history_start, trade_date)
        except DataProviderError as exc:
            index_failures.append(f"{index_symbol}: {exc}")
    market_environment = evaluate_market_environment(
        {symbol: route["data"] for symbol, route in index_routes.items()},
        generated_at=retrieved_at,
    )
    if index_failures:
        market_environment.summary.setdefault("warnings", []).extend(
            [f"大盘指数获取失败: {failure}" for failure in index_failures]
        )
        market_environment.summary["warnings"] = list(dict.fromkeys(market_environment.summary["warnings"]))
        if market_environment.summary.get("status") == "validated":
            market_environment.summary["status"] = "validated_with_warning"

    market_environment_paths: dict[str, dict[str, str]] = {}
    for index_symbol, route in index_routes.items():
        secondary_paths: list[dict[str, str]] = []
        for audit in route.get("secondary_audits", []):
            comparison = audit.get("comparison") if isinstance(audit, dict) else None
            audit_paths = save_secondary_market_audit(
                index_symbol,
                str(audit.get("source", "unknown")),
                trade_date,
                audit.get("raw_data"),
                comparison if isinstance(comparison, dict) else {},
                quality_report=audit.get("quality_report"),
                retrieved_at=audit.get("retrieved_at", route.get("retrieved_at", retrieved_at)),
                data_version=audit.get("data_version"),
                error=audit.get("error"),
            )
            audit["paths"] = audit_paths
            if isinstance(comparison, dict):
                comparison.update(
                    {
                        "raw_path": audit_paths.get("raw_path"),
                        "audit_path": audit_paths.get("audit_path"),
                        "retrieved_at": audit.get("retrieved_at"),
                    }
                )
            secondary_paths.append(audit_paths)
        route["secondary_audit_paths"] = secondary_paths
        market_environment_paths[index_symbol] = save_market_data(
            index_symbol,
            route["data"],
            source=route["source"],
            trade_date=trade_date,
            raw_data=route.get("raw_data"),
            rejected_data=route.get("rejected_data"),
            quality_report=route.get("quality_report"),
            retrieved_at=route.get("retrieved_at", retrieved_at),
            data_version=route.get("data_version"),
            frequency="1d",
            price_adjustment="none",
        )
        market_environment_paths[index_symbol]["secondary_audits"] = secondary_paths
    news_result: dict[str, Any] | None = None
    news_error = ""
    news_dimension: dict[str, Any] | None = None
    try:
        query = f"{args.query} {' '.join(args.symbol)}".strip()
        routed_news = router.search_news(query, time_range=args.news_time_range, max_results=args.max_news)
        raw_response = routed_news["data"]
        items = deduplicate_news(
            normalize_tavily_response(
                raw_response, query=query, retrieved_at=retrieved_at, related_symbols=args.symbol
            )
        )
        cutoff_result = filter_news_by_cutoff(
            items, cutoff=trade_date, timezone_name=settings.data_timezone
        )
        score_result = evaluate_news(
            items, cutoff=trade_date, timezone_name=settings.data_timezone, generated_at=retrieved_at
        )
        paths = NewsStore().save_batch(
            "tavily", raw_response, [*cutoff_result.eligible, *cutoff_result.excluded],
            retrieved_at=retrieved_at,
        )
        news_result = {
            "route": routed_news,
            "items": [*cutoff_result.eligible, *cutoff_result.excluded],
            "eligible_items": cutoff_result.eligible,
            "excluded_items": cutoff_result.excluded,
            "cutoff_report": cutoff_result.report,
            "score": score_result,
            "paths": paths,
        }
        news_dimension = score_result.to_dimension()
    except (DataProviderError, ValueError) as exc:
        news_error = str(exc)

    for symbol in args.symbol:
        try:
            routed = router.fetch_daily_bars(symbol, history_start, trade_date)
            fund_flow_error = ""
            fund_flow_result = evaluate_fund_flow(pd.DataFrame(), generated_at=retrieved_at)
            fund_flow_paths: dict[str, Any] = {}
            try:
                fund_flow_route = router.fetch_fund_flow(symbol, history_start, trade_date)
                fund_flow_result = evaluate_fund_flow(fund_flow_route["data"], generated_at=retrieved_at)
                secondary_flow_paths: list[dict[str, str]] = []
                for audit in fund_flow_route.get("secondary_audits", []):
                    comparison = audit.get("comparison") if isinstance(audit, dict) else None
                    audit_paths = save_secondary_fund_flow_audit(
                        symbol, str(audit.get("source", "unknown")), trade_date, audit.get("raw_data"),
                        comparison if isinstance(comparison, dict) else {},
                        quality_report=audit.get("quality_report"),
                        retrieved_at=audit.get("retrieved_at", fund_flow_route.get("retrieved_at", retrieved_at)),
                        data_version=audit.get("data_version"), error=audit.get("error"),
                    )
                    audit["paths"] = audit_paths
                    secondary_flow_paths.append(audit_paths)
                fund_flow_paths = save_fund_flow(
                    symbol, fund_flow_route["data"], source=fund_flow_route["source"], trade_date=trade_date,
                    raw_data=fund_flow_route.get("raw_data"), rejected_data=fund_flow_route.get("rejected_data"),
                    quality_report=fund_flow_route.get("quality_report"),
                    retrieved_at=fund_flow_route.get("retrieved_at", retrieved_at),
                    data_version=fund_flow_route.get("data_version"),
                )
                fund_flow_paths["secondary_audits"] = secondary_flow_paths
                routed["fund_flow_route"] = fund_flow_route
            except (DataProviderError, ValueError) as exc:
                fund_flow_error = str(exc)
                fund_flow_result.summary.setdefault("warnings", []).append(f"资金行为数据获取失败: {fund_flow_error}")
                fund_flow_result.summary["warnings"] = list(dict.fromkeys(fund_flow_result.summary["warnings"]))
            routed["fund_flow"] = fund_flow_result
            routed["fund_flow_paths"] = fund_flow_paths
            routed["fund_flow_error"] = fund_flow_error
            gate = evaluate_prediction_input(
                routed["data"],
                routed.get("quality_report"),
                symbol=symbol,
                start_date=history_start,
                end_date=trade_date,
                retrieved_at=routed.get("retrieved_at", retrieved_at),
                route_degraded=bool(routed.get("degraded")),
            )
            routed["prediction_quality_gate"] = gate.to_dict()
            routed.setdefault("quality_report", {})["prediction_quality_gate"] = gate.to_dict()
            routed["market_environment"] = market_environment
            routed["market_environment_routes"] = index_routes
            routed["market_environment_paths"] = market_environment_paths
            indicator_error = ""
            signal_error = ""
            prediction_error = ""
            if gate.can_predict:
                try:
                    indicator_result = calculate_technical_indicators(
                        routed["data"],
                        routed.get("quality_report"),
                        symbol=symbol,
                        start_date=history_start,
                        end_date=trade_date,
                        retrieved_at=routed.get("retrieved_at", retrieved_at),
                        route_degraded=bool(routed.get("degraded")),
                    )
                    indicator_paths = save_technical_indicators(
                        symbol,
                        indicator_result.data,
                        source=routed["source"],
                        trade_date=trade_date,
                        indicator_summary=indicator_result.summary,
                        quality_gate=indicator_result.quality_gate.to_dict(),
                        retrieved_at=routed.get("retrieved_at", retrieved_at),
                        data_version=routed.get("data_version"),
                    )
                    routed["technical_indicators"] = indicator_result
                    routed["technical_indicator_paths"] = indicator_paths
                    try:
                        signal_result = generate_technical_signals(indicator_result, symbol=symbol)
                        signal_paths = save_technical_signals(
                            symbol,
                            signal_result.data,
                            source=routed["source"],
                            trade_date=trade_date,
                            signal_summary=signal_result.summary,
                            quality_gate=signal_result.quality_gate.to_dict(),
                            retrieved_at=routed.get("retrieved_at", retrieved_at),
                            data_version=routed.get("data_version"),
                        )
                        routed["technical_signals"] = signal_result
                        routed["technical_signal_paths"] = signal_paths
                        try:
                            prediction_result = generate_next_day_prediction(
                                signal_result,
                                symbol=symbol,
                                market_environment=market_environment.to_dimension(),
                                fund_flow=fund_flow_result.to_dimension(),
                                news=news_dimension,
                                generated_at=retrieved_at,
                            )
                            prediction_paths = save_next_day_prediction(
                                symbol,
                                prediction_result,
                                trade_date=trade_date,
                                source=routed["source"],
                                retrieved_at=routed.get("retrieved_at", retrieved_at),
                                data_version=routed.get("data_version"),
                            )
                            routed["next_day_prediction"] = prediction_result
                            routed["next_day_prediction_paths"] = prediction_paths
                        except (PredictionInputBlockedError, NextDayPredictionError) as exc:
                            prediction_error = str(exc)
                            routed["next_day_prediction_error"] = prediction_error
                    except (PredictionInputBlockedError, TechnicalSignalError) as exc:
                        signal_error = str(exc)
                        prediction_error = "技术信号未生成，跳过次日预测"
                        routed["technical_signal_error"] = signal_error
                        routed["next_day_prediction_error"] = prediction_error
                except (PredictionInputBlockedError, TechnicalIndicatorError) as exc:
                    indicator_error = str(exc)
                    signal_error = "技术指标未生成，跳过技术信号计算"
                    prediction_error = "技术指标未生成，跳过次日预测"
                    routed["technical_indicator_error"] = indicator_error
                    routed["technical_signal_error"] = signal_error
                    routed["next_day_prediction_error"] = prediction_error
            else:
                indicator_error = "预测输入门禁为 blocked，跳过技术指标和技术信号计算"
                signal_error = indicator_error
                prediction_error = "预测输入门禁为 blocked，跳过次日预测"
                routed["technical_indicator_error"] = indicator_error
                routed["technical_signal_error"] = signal_error
                routed["next_day_prediction_error"] = prediction_error
            secondary_paths: list[dict[str, str]] = []
            for audit in routed.get("secondary_audits", []):
                comparison = audit.get("comparison") if isinstance(audit, dict) else None
                audit_paths = save_secondary_market_audit(
                    symbol,
                    str(audit.get("source", "unknown")),
                    trade_date,
                    audit.get("raw_data"),
                    comparison if isinstance(comparison, dict) else {},
                    quality_report=audit.get("quality_report"),
                    retrieved_at=audit.get("retrieved_at", routed.get("retrieved_at", retrieved_at)),
                    data_version=audit.get("data_version"),
                    error=audit.get("error"),
                )
                audit["paths"] = audit_paths
                if isinstance(comparison, dict):
                    comparison.update(
                        {
                            "raw_path": audit_paths.get("raw_path"),
                            "audit_path": audit_paths.get("audit_path"),
                            "retrieved_at": audit.get("retrieved_at"),
                        }
                    )
                secondary_paths.append(audit_paths)

            paths = save_market_data(
                symbol,
                routed["data"],
                source=routed["source"],
                trade_date=trade_date,
                raw_data=routed.get("raw_data"),
                rejected_data=routed.get("rejected_data"),
                quality_report=routed.get("quality_report"),
                retrieved_at=routed.get("retrieved_at", retrieved_at),
                data_version=routed.get("data_version"),
            )
            paths["secondary_audits"] = secondary_paths
            market_results.append(
                {
                    "symbol": symbol,
                    "route": routed,
                    "paths": paths,
                    "indicator_error": indicator_error,
                    "signal_error": signal_error,
                    "prediction_error": prediction_error,
                    "fund_flow_error": fund_flow_error,
                }
            )
        except (DataProviderError, ValueError) as exc:
            failures.append(f"{symbol}: {exc}")

    report_path = Path(args.report) if args.report else ROOT / "reports" / f"daily_{trade_date.isoformat()}.md"
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(args, trade_date, retrieved_at, market_results, failures, news_result, news_error, report_path), encoding="utf-8")
    print(f"报告已生成: {report_path}")
    print(f"Markdown绝对路径链接: [{report_path}]({report_path.as_uri()})")
    print(f"行情成功: {len(market_results)}，行情失败: {len(failures)}，新闻: {len(news_result['eligible_items']) if news_result else 0}")
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
        gate_checks = route.get("prediction_quality_gate", {}).get("checks", {})
        data_range = gate_checks.get("requested_range", {})
        lines.append(f"- 指标历史请求区间：`{data_range.get('start_date', '未知')}` 至 `{data_range.get('end_date', '未知')}`")
        lines.append(f"- 质量状态：`{quality.get('status', 'unknown')}`；规则版本：`{quality.get('quality_rules_version', 'unknown')}`")
        gate = route.get("prediction_quality_gate", quality.get("prediction_quality_gate", {}))
        lines.append(
            f"- 预测输入门禁：`{gate.get('status', 'unknown')}`；允许进入预测：`{gate.get('can_predict', False)}`；"
            f"数据截止：`{gate.get('data_cutoff', '未知')}`"
        )
        for reason in gate.get("reasons", []):
            lines.append(f"- 预测阻断原因：{reason}")
        for warning in gate.get("warnings", []):
            lines.append(f"- 预测输入警告：{warning}")
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
            lines.append(
                f"  - 状态：`{detail.get('status', 'unknown')}`；获取时间：`{detail.get('retrieved_at', 'unknown')}`"
            )
            if detail.get("raw_path"):
                lines.append(f"  - 备用源原始响应：`{detail['raw_path']}`")
            if detail.get("audit_path"):
                lines.append(f"  - 交叉验证明细：`{detail['audit_path']}`")
            for warning in detail.get("warnings", []):
                lines.append(f"  - 待核验警告：{warning}")
        lines.append(f"- 保存文件：`{result['paths']['data_path']}`")
        if result["paths"].get("raw_path"):
            lines.append(f"- 原始数据：`{result['paths']['raw_path']}`")
        lines.append(f"- 质量报告：`{result['paths']['quality_path']}`")
        if result["paths"].get("rejected_path"):
            lines.append(f"- 异常行：`{result['paths']['rejected_path']}`")
        fund_flow_result = route.get("fund_flow")
        if fund_flow_result is not None:
            lines.extend(_render_fund_flow_report(fund_flow_result, route.get("fund_flow_paths", {})))
        elif route.get("fund_flow_error"):
            lines.append(f"- 资金行为：未获取（{route['fund_flow_error']}）")
        market_environment_result = route.get("market_environment")
        if market_environment_result is not None:
            lines.extend(
                _render_market_environment_report(
                    market_environment_result,
                    route.get("market_environment_paths", {}),
                )
            )
        indicator_result = route.get("technical_indicators")
        if indicator_result is not None:
            lines.extend(_render_indicator_report(indicator_result, route.get("technical_indicator_paths", {})))
        elif result.get("indicator_error"):
            lines.append(f"- 技术指标：未计算（{result['indicator_error']}）")
        signal_result = route.get("technical_signals")
        if signal_result is not None:
            lines.extend(_render_signal_report(signal_result, route.get("technical_signal_paths", {})))
        elif result.get("signal_error"):
            lines.append(f"- 技术信号：未计算（{result['signal_error']}）")
        prediction_result = route.get("next_day_prediction")
        if prediction_result is not None:
            lines.extend(
                _render_prediction_report(
                    prediction_result,
                    route.get("next_day_prediction_paths", {}),
                )
            )
        elif result.get("prediction_error"):
            lines.append(f"- 次日预测：未计算（{result['prediction_error']}）")
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
        lines.append(f"- 结果数：{len(news_result['items'])}；有效数：{len(news_result.get('eligible_items', []))}；排除数：{len(news_result.get('excluded_items', []))}")
        cutoff_report = news_result.get("cutoff_report", {})
        lines.append(f"- 信息截面：`{cutoff_report.get('cutoff', '未知')}`；截面状态：`{cutoff_report.get('status', 'unknown')}`")
        score_result = news_result.get("score")
        if score_result is not None:
            score_summary = score_result.summary
            score = score_result.score
            score_text = "NA" if score is None else f"{float(score):+.3f}"
            lines.append(
                f"- 消息面评分：`{score_text}`；数据截止：`{score_result.data_cutoff or '未知'}`；"
                f"状态：`{score_summary.get('status', 'unknown')}`"
            )
            for evidence in score_summary.get("evidence", []):
                lines.append(f"- 消息证据：{evidence}")
            for warning in score_summary.get("warnings", []):
                lines.append(f"- 消息警告：{warning}")
        lines.append(f"- 原始数据：`{news_result['paths']['raw_path']}`")
        lines.append(f"- 标准化数据：`{news_result['paths']['processed_path']}`")
        lines.append("")
        for item in news_result["items"]:
            title = item["title"] or item["url"]
            status = item.get("cutoff_status", "unknown")
            lines.append(f"- [{title}]({item['url']})（发布时间：{item.get('published_at') or '未知'}；截面：`{status}`）")
    else:
        lines.append(f"- 新闻搜索失败：{news_error or '未返回结果'}")
    lines.extend(["", "## 数据限制", "", "- 行情统一为未复权日线；实时快照、分钟线和 Tick 不在本轮处理范围。", "- 预测必须先通过预测输入质量门禁；`blocked` 数据不得进入预测。", "- Tavily 用于新闻搜索，不代表实时行情。", "- 本报告仅记录采集结果，不构成投资建议。", ""])
    return "\n".join(lines)



def _render_fund_flow_report(fund_flow_result: Any, paths: dict[str, Any]) -> list[str]:
    """把资金行为评分、数据截止时间和审计路径写入每日报告。"""
    summary = fund_flow_result.summary
    score = summary.get("score", fund_flow_result.score)
    score_text = "NA" if score is None else f"{float(score):+.3f}"
    lines = ["", "#### 资金行为", ""]
    lines.append(
        f"- 资金行为评分：`{score_text}`；状态：`{summary.get('status', 'unknown')}`；"
        f"数据截止：`{fund_flow_result.data_cutoff or '未知'}`；来源：`{summary.get('source') or '未知'}`"
    )
    if summary.get("latest_net_flow_amount") is not None:
        lines.append(f"- 最新主力净流入：`{summary['latest_net_flow_amount'] / 10000:+.2f} 万元`；强度：`{summary.get('intensity_score', 'NA')}`；连续性：`{summary.get('consistency_score', 'NA')}`")
    if paths.get("data_path"):
        lines.append(f"- 资金流向数据：`{paths['data_path']}`")
    if paths.get("quality_path"):
        lines.append(f"- 资金流向质量报告：`{paths['quality_path']}`")
    for audit in paths.get("secondary_audits", []):
        if audit.get("audit_path"):
            lines.append(f"- 资金流向备用源审计：`{audit['audit_path']}`")
    for evidence in summary.get("evidence", []):
        lines.append(f"- 资金证据：{evidence}")
    for warning in summary.get("warnings", []):
        lines.append(f"- 资金警告：{warning}")
    return lines


def _render_market_environment_report(environment_result: Any, paths: dict[str, dict[str, str]]) -> list[str]:
    """把大盘环境评分、指数证据和数据路径写入每日报告。"""
    summary = environment_result.summary
    lines = ["", "#### 大盘环境", ""]
    score = summary.get("score", environment_result.score)
    score_text = "NA" if score is None else f"{float(score):+.3f}"
    lines.append(
        f"- 环境评分：`{score_text}`；状态：`{summary.get('status', 'unknown')}`；"
        f"数据截止：`{environment_result.data_cutoff or '未知'}`；来源：`{summary.get('source') or '未知'}`"
    )
    breadth = summary.get("breadth", {})
    if breadth.get("available"):
        lines.append(
            f"- 市场宽度：上涨家数 `{breadth.get('advancing')}`；"
            f"下跌家数 `{breadth.get('declining')}`；评分 `{breadth.get('score', 'NA')}`"
        )
    for symbol, item in summary.get("indices", {}).items():
        lines.append(
            f"- 指数 `{item.get('label', symbol)}`："
            f"评分 `{item.get('score', 'NA')}`；5日涨跌 `{item.get('return_5d', 'NA')}`；"
            f"最新交易日 `{item.get('latest_trade_date', '未知')}`"
        )
        saved = paths.get(symbol, {})
        if saved.get("data_path"):
            lines.append(f"  - 指数行情：`{saved['data_path']}`")
        if saved.get("raw_path"):
            lines.append(f"  - 指数原始数据：`{saved['raw_path']}`")
    for evidence in summary.get("evidence", []):
        lines.append(f"- 大盘证据：{evidence}")
    for warning in summary.get("warnings", []):
        lines.append(f"- 大盘环境警告：{warning}")
    return lines

def _render_indicator_report(indicator_result: Any, paths: dict[str, str]) -> list[str]:
    """把技术指标最新值和可用性摘要写入每日报告。"""
    summary = indicator_result.summary
    lines = ["", "#### 技术指标", ""]
    lines.append(f"- 历史记录数：`{summary.get('history_rows', 0)}`；指标列数：`{len(summary.get('indicator_columns', []))}`")
    lines.append(f"- 指标门禁：`{indicator_result.quality_gate.status}`；数据截止：`{summary.get('latest_trade_date', '未知')}`")
    if paths.get("data_path"):
        lines.append(f"- 指标数据：`{paths['data_path']}`")
    if paths.get("metadata_path"):
        lines.append(f"- 指标元数据：`{paths['metadata_path']}`")

    data = indicator_result.data
    if not data.empty:
        latest = data.iloc[-1]
        preferred = (
            "ma_5",
            "ma_20",
            "ma_60",
            "macd_dif",
            "macd_dea",
            "macd_hist",
            "rsi_6",
            "rsi_14",
            "bollinger_upper_20",
            "bollinger_lower_20",
            "atr_14",
            "support_20",
            "resistance_20",
            "return_1d",
            "volatility_20",
        )
        values = [f"{column}={_format_indicator_value(latest[column])}" for column in preferred if column in data]
        if values:
            lines.append(f"- 最新指标（{latest.get('trade_date', '未知')}）：" + "；".join(values))
    for warning in summary.get("warnings", []):
        lines.append(f"- 指标警告：{warning}")
    return lines


def _render_signal_report(signal_result: Any, paths: dict[str, str]) -> list[str]:
    """把最新技术信号、解释和风险条件写入每日报告。"""
    summary = signal_result.summary
    lines = ["", "#### 技术信号", ""]
    lines.append(
        f"- 综合方向：`{summary.get('direction', 'neutral')}`；综合分数：`{summary.get('composite_score', 'NA')}`；"
        f"信号强度：`{summary.get('signal_strength_label', 'weak')}`（{summary.get('signal_strength', 0):.2f}%）；"
        f"置信度：`{summary.get('confidence', 0):.3f}`"
    )
    lines.append(f"- 信号数据截止：`{summary.get('latest_trade_date', '未知')}`；最新收盘：`{_format_indicator_value(summary.get('latest_close'))}`")
    if paths.get("data_path"):
        lines.append(f"- 信号数据：`{paths['data_path']}`")
    if paths.get("metadata_path"):
        lines.append(f"- 信号元数据：`{paths['metadata_path']}`")
    components = summary.get("components", {})
    component_text = "；".join(
        f"{name}={value.get('label', 'unknown')}({value.get('score', 'NA')})"
        for name, value in components.items()
    )
    if component_text:
        lines.append(f"- 信号分解：{component_text}")
    volatility = summary.get("volatility", {})
    if volatility:
        lines.append(f"- 波动率：`{volatility.get('level', 'unknown')}`；{volatility.get('evidence', '')}")
    levels = summary.get("levels", {})
    if levels:
        lines.append(
            f"- 支撑/压力位置：`{levels.get('context', 'unknown')}`；"
            f"20日支撑距现价 {levels.get('support_distance_pct', 'NA')}%；"
            f"20日压力距现价 {levels.get('resistance_distance_pct', 'NA')}%"
        )
    for trigger in summary.get("triggers", []):
        lines.append(f"- 触发条件：{trigger}")
    for invalidation in summary.get("invalidations", []):
        lines.append(f"- 失效条件：{invalidation}")
    for warning in summary.get("warnings", []):
        lines.append(f"- 信号警告：{warning}")
    return lines


def _render_prediction_report(prediction_result: Any, paths: dict[str, str]) -> list[str]:
    """把次日预测的概率、区间和覆盖限制写入每日报告。"""
    summary = prediction_result.summary
    probabilities = summary.get("probabilities", {})
    price_range = summary.get("price_range", {})
    lines = ["", "#### 次日预测", ""]
    lines.append(
        f"- 预测方向：`{summary.get('direction', 'neutral')}`；"
        f"置信度：`{summary.get('confidence', 0):.3f}`；"
        f"数据覆盖率：`{summary.get('coverage', 0):.2%}`"
    )
    lines.append(
        f"- 方向概率：看多 `{probabilities.get('bullish', 0):.2%}`；"
        f"震荡 `{probabilities.get('neutral', 0):.2%}`；"
        f"看空 `{probabilities.get('bearish', 0):.2%}`"
    )
    lines.append(
        f"- 预期变动：`{summary.get('expected_return_pct', 0):.4f}%`；"
        f"价格区间：`{price_range.get('lower', 'NA')}` ~ `{price_range.get('upper', 'NA')}`"
    )
    lines.append(
        f"- 预测数据截止：`{prediction_result.data_cutoff or '未知'}`；"
        f"目标：`{prediction_result.target_trade_date or '下一交易日'}`"
    )
    if paths.get("prediction_path"):
        lines.append(f"- 预测结果：`{paths['prediction_path']}`")
    for name, component in summary.get("components", {}).items():
        status = "可用" if component.get("available") else "不可用"
        lines.append(
            f"- 预测维度 `{name}`：{status}；基础权重 {component.get('base_weight', 0):.0%}；"
            f"有效权重 {component.get('effective_weight', 0):.2%}；评分 {component.get('score', 'NA')}"
        )
    for warning in summary.get("warnings", []):
        lines.append(f"- 预测警告：{warning}")
    for trigger in summary.get("triggers", []):
        lines.append(f"- 预测触发条件：{trigger}")
    for invalidation in summary.get("invalidations", []):
        lines.append(f"- 预测失效条件：{invalidation}")
    return lines


def _format_indicator_value(value: Any) -> str:
    try:
        if value is None or value != value:
            return "NA"
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
