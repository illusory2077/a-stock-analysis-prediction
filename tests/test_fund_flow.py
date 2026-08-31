from datetime import date, datetime, timezone

import pandas as pd

from src.analysis import evaluate_fund_flow
from src.data_providers import DataProvider, DataSourceRouter
from src.market import compare_fund_flow, normalize_fund_flow, validate_fund_flow


class FundFlowProvider(DataProvider):
    name = "tushare-test"

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": True}

    def daily_bars(self, symbol, start_date, end_date):
        raise AssertionError("资金流向测试不应调用 daily_bars")

    def fund_flow(self, symbol, start_date, end_date):
        return [
            {
                "ts_code": symbol,
                "trade_date": "2026-08-19",
                "net_mf_amount": 2.0,
                "buy_elg_amount": 3.0,
                "sell_elg_amount": 1.0,
            },
            {
                "ts_code": symbol,
                "trade_date": "2026-08-18",
                "net_mf_amount": 1.0,
            },
        ]


def test_normalize_tushare_fund_flow_converts_amounts_to_cny() -> None:
    normalized = normalize_fund_flow(
        [{"ts_code": "600519.SH", "trade_date": "2026-08-19", "net_mf_amount": 2.0, "buy_elg_amount": 3.0, "sell_elg_amount": 1.0}],
        symbol="600519.SH", source="tushare", retrieved_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    assert normalized.iloc[0]["net_flow_amount"] == 20000
    assert normalized.iloc[0]["super_large_net_amount"] == 20000
    assert normalized.iloc[0]["currency"] == "CNY"


def test_normalize_akshare_fund_flow_keeps_yuan_amounts() -> None:
    normalized = normalize_fund_flow(
        [{"代码": "600519", "日期": "2026-08-19", "主力净流入-净额": 120000, "主力净流入-净占比": 2.5}],
        symbol="600519.SH", source="akshare",
    )

    assert normalized.iloc[0]["symbol"] == "600519.SH"
    assert normalized.iloc[0]["net_flow_amount"] == 120000
    assert normalized.iloc[0]["net_flow_pct"] == 2.5


def test_fund_flow_quality_and_score_are_auditable() -> None:
    data = normalize_fund_flow(
        [{"ts_code": "600519.SH", "trade_date": f"2026-08-{day:02d}", "net_mf_amount": amount} for day, amount in [(14, -2.0), (15, -1.0), (18, 2.0), (19, 3.0)]],
        symbol="600519.SH", source="tushare",
    )
    quality = validate_fund_flow(data, rejected_data=data.attrs["rejected_data"], input_rows=data.attrs["input_rows"])
    result = evaluate_fund_flow(quality.data)

    assert quality.report["status"] == "validated"
    assert result.score is not None and result.score > 0
    assert result.data_cutoff == "2026-08-19"
    assert result.to_dimension()["available"] is True


def test_router_fetch_fund_flow_uses_optional_fund_flow_adapter() -> None:
    router = DataSourceRouter(market_providers=[FundFlowProvider()], news_providers=[], cross_validate_market=False, validate_calendar=False)
    result = router.fetch_fund_flow("600519.SH", date(2026, 8, 18), date(2026, 8, 19))

    assert result["source"] == "tushare-test"
    assert result["data"].iloc[-1]["net_flow_amount"] == 20000
    assert result["quality_report"]["cross_validation"]["status"] == "skipped"


def test_compare_fund_flow_marks_large_difference() -> None:
    left = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "net_flow_amount": [100.0]})
    right = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "net_flow_amount": [200.0]})

    comparison = compare_fund_flow(left, right, primary_source="a", secondary_source="b", amount_threshold=0.2)

    assert comparison["status"] == "mismatch"
    assert comparison["metrics"]["net_flow_amount"]["max_diff_pct"] == 50.0
