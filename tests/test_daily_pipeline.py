from __future__ import annotations

from argparse import Namespace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.run_daily_pipeline import _render_disclosure_report, _render_report, build_parser
from src.analysis import (
    calculate_technical_indicators,
    evaluate_fund_flow,
    evaluate_market_environment,
    evaluate_news,
    generate_next_day_prediction,
    generate_technical_signals,
)


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
            "source": "test",
            "retrieved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )


def _report() -> dict:
    return {
        "status": "validated",
        "errors": [],
        "warnings": [],
        "rejected_rows": 0,
        "data_range": {"start_date": "2026-01-05", "end_date": "2026-04-10"},
        "calendar_validation": {"status": "validated"},
        "cross_validation": {"status": "matched"},
    }


def test_parser_exposes_indicator_lookback() -> None:
    args = build_parser().parse_args(["--symbol", "600519.SH"])
    assert args.lookback_days == 120


def _fund_flow() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": "600519.SH",
            "exchange": "SSE",
            "asset_type": "stock",
            "trade_date": pd.date_range("2026-04-06", periods=5, freq="B").date,
            "net_flow_amount": [10000, 15000, 20000, 30000, 50000],
            "currency": "CNY",
            "source": "test-fund-flow",
        }
    )


def test_render_report_includes_latest_technical_indicators(tmp_path: Path) -> None:
    result = calculate_technical_indicators(_bars(), _report(), symbol="600519.SH")
    args = Namespace(symbol=["600519.SH"])
    signals = generate_technical_signals(result)
    environment = evaluate_market_environment({"000001.SH": _bars()})
    margin = pd.DataFrame({
        "trade_date": [date(2026, 4, 9), date(2026, 4, 10)],
        "margin_balance": [100000.0, 120000.0],
        "short_balance": [50000.0, 48000.0],
        "source": "test-margin",
    })
    dragon_tiger = pd.DataFrame({
        "trade_date": [date(2026, 4, 10)],
        "net_buy_amount": [30000.0],
        "institution_net_amount": [10000.0],
        "source": "test-dragon",
    })
    fund_flow = evaluate_fund_flow(
        _fund_flow(),
        margin_data=margin,
        dragon_tiger_data=dragon_tiger,
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    prediction = generate_next_day_prediction(
        signals,
        market_environment=environment.to_dimension(),
        fund_flow=fund_flow.to_dimension(),
        symbol="600519.SH",
        target_trade_date=date(2026, 4, 13),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    route = {
        "source": "test",
        "degraded": False,
        "data": result.data,
        "quality_report": _report(),
        "prediction_quality_gate": result.quality_gate.to_dict(),
        "technical_indicators": result,
        "technical_indicator_paths": {"data_path": "indicators.parquet"},
        "technical_signals": signals,
        "technical_signal_paths": {"data_path": "signals.parquet", "metadata_path": "signals_metadata.json"},
        "market_environment": environment,
        "market_environment_paths": {
            "000001.SH": {"data_path": "market/000001.SH.parquet"},
        },
        "fund_flow": fund_flow,
        "fund_flow_paths": {
            "data_path": "fund_flow/600519.SH.parquet",
            "quality_path": "fund_flow/600519.SH_quality.json",
            "secondary_audits": [
                {"audit_path": "fund_flow/audit/600519.SH_akshare.json"},
            ],
        },
        "margin_paths": {
            "data_path": "margin/600519.SH.parquet",
            "quality_path": "margin/600519.SH_quality.json",
        },
        "dragon_tiger_paths": {
            "data_path": "dragon_tiger/600519.SH.parquet",
            "quality_path": "dragon_tiger/600519.SH_quality.json",
        },
        "next_day_prediction": prediction,
        "next_day_prediction_paths": {
            "prediction_path": "predictions/600519.SH_2026-04-10_next_day_prediction.json",
        },
    }
    report = _render_report(
        args,
        date(2026, 4, 10),
        datetime(2026, 8, 31, tzinfo=timezone.utc),
        [{"symbol": "600519.SH", "route": route, "paths": {"data_path": "bars.parquet", "quality_path": "quality.json"}}],
        [],
        None,
        "",
        tmp_path / "daily.md",
    )

    assert "#### 技术指标" in report
    assert "ma_5=" in report
    assert "indicators.parquet" in report
    assert "#### 技术信号" in report
    assert "综合方向：`bullish`" in report
    assert "signals.parquet" in report
    assert "#### 大盘环境" in report
    assert "环境评分：`" in report
    assert "上证指数" in report
    assert "market/000001.SH.parquet" in report
    assert "#### 资金行为" in report
    assert "资金行为评分：`" in report
    assert "最新主力净流入：`+5.00 万元`" in report
    assert "fund_flow/600519.SH.parquet" in report
    assert "fund_flow/600519.SH_quality.json" in report
    assert "fund_flow/audit/600519.SH_akshare.json" in report
    assert "融资融券补充评分：`" in report
    assert "margin/600519.SH.parquet" in report
    assert "龙虎榜补充评分：`" in report
    assert "dragon_tiger/600519.SH.parquet" in report
    assert "#### 次日预测" in report
    assert "方向概率：看多" in report
    assert "fund_flow" in report
    assert "价格区间：`" in report
    assert "置信度：`" in report
    assert "预测维度未接入真实数据" in report
    assert "600519.SH_2026-04-10_next_day_prediction.json" in report


def test_render_report_includes_news_cutoff_and_score(tmp_path: Path) -> None:
    args = Namespace(symbol=["600519.SH"])
    news = evaluate_news(
        [
            {
                "news_id": "n1",
                "title": "公司业绩增长并获批新订单",
                "published_at": "2026-04-10T15:00:00+08:00",
                "source": "example.com",
                "score": 0.9,
            },
            {
                "news_id": "n2",
                "title": "公司被处罚",
                "published_at": "2026-04-11T09:00:00+08:00",
                "source": "example.com",
                "score": 0.9,
            },
        ],
        cutoff=date(2026, 4, 10),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    report = _render_report(
        args,
        date(2026, 4, 10),
        datetime(2026, 8, 31, tzinfo=timezone.utc),
        [],
        [],
        {
            "route": {"source": "tavily", "degraded": False},
            "items": [{
                "title": "公司业绩增长并获批新订单",
                "url": "https://example.com/n1",
                "published_at": "2026-04-10T15:00:00+08:00",
                "cutoff_status": "eligible",
            }],
            "eligible_items": [{"news_id": "n1"}],
            "excluded_items": [{"news_id": "n2"}],
            "cutoff_report": {"status": "validated_with_warning", "cutoff": "2026-04-10T23:59:59.999999+08:00"},
            "score": news,
            "paths": {"raw_path": "raw/news/tavily/2026-04-10/raw.json", "processed_path": "processed/news/news_20260410.jsonl"},
        },
        "",
        tmp_path / "daily.md",
    )

    assert "消息面评分：`" in report
    assert "信息截面：`2026-04-10T23:59:59.999999+08:00`" in report
    assert "有效数：1；排除数：1" in report
    assert "截面：`eligible`" in report


def test_render_disclosure_report_includes_cutoff_counts_and_paths() -> None:
    result = evaluate_news(
        [{
            "news_id": "n1", "title": "市场消息",
            "published_at": "2026-08-31T08:00:00+08:00",
        }],
        disclosures=[
            {
                "source_record_id": "a1", "report_type": "announcement",
                "title": "公司获批订单", "published_at": "2026-08-31T09:00:00+08:00",
            },
            {
                "source_record_id": "f1", "report_type": "financial_report",
                "title": "业绩增长", "published_at": "2026-08-31T10:00:00+08:00",
            },
            {
                "source_record_id": "a2", "report_type": "announcement",
                "title": "未来公告", "published_at": "2026-09-01T09:00:00+08:00",
            },
        ],
        cutoff=date(2026, 8, 31),
        generated_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    report = "\n".join(_render_disclosure_report(
        result,
        pd.DataFrame([{"source_record_id": "a1"}, {"source_record_id": "f1"}]),
        {
            "processed_path": "processed/disclosures/600519.SH_2026-08-31.jsonl",
            "quality_path": "processed/disclosures/600519.SH_2026-08-31_quality.json",
            "raw_path": "raw/disclosures/tushare/2026-08-31/raw.json",
        },
    ))

    assert "#### 公告与财报" in report
    assert "有效公告：`1`" in report
    assert "有效财报：`1`" in report
    assert "排除：`1`" in report
    assert "processed/disclosures/600519.SH_2026-08-31.jsonl" in report
