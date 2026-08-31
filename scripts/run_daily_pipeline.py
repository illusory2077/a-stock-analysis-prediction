from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis import (  # noqa: E402
    PredictionInputBlockedError,
    TechnicalIndicatorError,
    TechnicalSignalError,
    calculate_technical_indicators,
    generate_technical_signals,
    evaluate_prediction_input,
)
from src.data_providers import DataProviderError, DataSourceRouter  # noqa: E402
from src.news import NewsStore, deduplicate_news, normalize_tavily_response  # noqa: E402
from src.storage import (  # noqa: E402
    save_market_data,
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

    for symbol in args.symbol:
        try:
            routed = router.fetch_daily_bars(symbol, history_start, trade_date)
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
            indicator_error = ""
            signal_error = ""
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
                    except (PredictionInputBlockedError, TechnicalSignalError) as exc:
                        signal_error = str(exc)
                        routed["technical_signal_error"] = signal_error
                except (PredictionInputBlockedError, TechnicalIndicatorError) as exc:
                    indicator_error = str(exc)
                    signal_error = "技术指标未生成，跳过技术信号计算"
                    routed["technical_indicator_error"] = indicator_error
                    routed["technical_signal_error"] = signal_error
            else:
                indicator_error = "预测输入门禁为 blocked，跳过技术指标和技术信号计算"
                signal_error = indicator_error
                routed["technical_indicator_error"] = indicator_error
                routed["technical_signal_error"] = signal_error
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
            market_results.append({"symbol": symbol, "route": routed, "paths": paths, "indicator_error": indicator_error, "signal_error": signal_error})
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
    lines.extend(["", "## 数据限制", "", "- 行情统一为未复权日线；实时快照、分钟线和 Tick 不在本轮处理范围。", "- 预测必须先通过预测输入质量门禁；`blocked` 数据不得进入预测。", "- Tavily 用于新闻搜索，不代表实时行情。", "- 本报告仅记录采集结果，不构成投资建议。", ""])
    return "\n".join(lines)



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

def _format_indicator_value(value: Any) -> str:
    try:
        if value is None or value != value:
            return "NA"
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
