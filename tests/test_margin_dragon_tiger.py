from datetime import date

import pandas as pd

from src.analysis import evaluate_fund_flow
from src.data_providers import AkshareProvider, DataProvider, DataSourceRouter, TushareProvider
from src.market import (
    compare_dragon_tiger,
    compare_margin,
    normalize_dragon_tiger,
    normalize_margin,
    validate_dragon_tiger,
    validate_margin,
)


class SpecialProvider(DataProvider):
    name = "tushare-test"

    def healthcheck(self):
        return {"provider": self.name, "configured": True, "ok": True}

    def daily_bars(self, symbol, start_date, end_date):
        return [{"symbol": symbol, "trade_date": start_date, "open": 1, "high": 1, "low": 1, "close": 1}]

    def margin(self, symbol, start_date, end_date):
        return [{"ts_code": symbol, "trade_date": "2026-08-19", "rzye": 100, "rzmre": 20, "rzche": 10, "rqye": 30}]

    def dragon_tiger(self, symbol, start_date, end_date):
        return [{"ts_code": symbol, "trade_date": "2026-08-19", "reason": "涨幅偏离", "close": 10, "pct_change": 2, "net_amount": 5, "buy": 8, "sell": 3}]


def test_margin_normalization_keeps_tushare_yuan_amounts_and_rejects_negative() -> None:
    data = normalize_margin(
        [
            {"ts_code": "600519.SH", "trade_date": "2026-08-19", "rzye": 100, "rzmre": 20, "rzche": 10, "rqye": 30},
            {"ts_code": "600519.SH", "trade_date": "2026-08-20", "rzye": -1},
        ],
        symbol="600519.SH",
        source="tushare",
    )
    assert data.iloc[0]["margin_balance"] == 100
    assert "short_sell_volume" in data.columns
    assert len(data.attrs["rejected_data"]) == 1
    quality = validate_margin(data, rejected_data=data.attrs["rejected_data"], input_rows=2)
    assert quality.report["status"] == "validated_with_warning"


def test_dragon_tiger_normalization_and_comparison() -> None:
    left = normalize_dragon_tiger(
        [{"ts_code": "600519.SH", "trade_date": "2026-08-19", "reason": "涨幅偏离", "close": 10, "net_amount": 10}],
        symbol="600519.SH",
        source="tushare",
    )
    right = normalize_dragon_tiger(
        [{"代码": "600519", "日期": "2026-08-19", "上榜原因": "涨幅偏离", "收盘价": 10, "净买入额": 20}],
        symbol="600519.SH",
        source="akshare",
    )
    assert left.iloc[0]["net_buy_amount"] == 10
    assert right.iloc[0]["net_buy_amount"] == 20
    assert compare_dragon_tiger(left, right, primary_source="tushare", secondary_source="akshare")["status"] == "mismatch"


def test_router_and_fund_flow_include_special_evidence() -> None:
    router = DataSourceRouter(
        market_providers=[SpecialProvider()],
        news_providers=[],
        cross_validate_market=False,
        validate_calendar=False,
    )
    margin_route = router.fetch_margin("600519.SH", date(2026, 8, 19), date(2026, 8, 19))
    dragon_route = router.fetch_dragon_tiger("600519.SH", date(2026, 8, 19), date(2026, 8, 19))
    result = evaluate_fund_flow(
        pd.DataFrame(),
        margin_data=margin_route["data"],
        dragon_tiger_data=dragon_route["data"],
    )
    assert margin_route["quality_report"]["cross_validation"]["status"] == "skipped"
    assert result.score is not None
    assert result.summary["components"]["margin"]["available"] is False
    assert result.summary["components"]["dragon_tiger"]["available"] is True
    assert any("龙虎榜" in item for item in result.summary["evidence"])


def test_compare_margin_uses_common_dates() -> None:
    left = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "margin_balance": [100.0]})
    right = pd.DataFrame({"trade_date": [date(2026, 8, 19)], "margin_balance": [110.0]})
    result = compare_margin(left, right, primary_source="a", secondary_source="b", amount_threshold=0.2)
    assert result["status"] == "matched"


def test_tushare_top_list_is_called_by_trade_date() -> None:
    calls: list[dict[str, str]] = []

    class Client:
        def top_list(self, **kwargs):
            calls.append(kwargs)
            if kwargs["trade_date"] == "20260820":
                return pd.DataFrame([{"ts_code": kwargs["ts_code"], "trade_date": kwargs["trade_date"], "net_amount": 10}])
            return pd.DataFrame()

    provider = object.__new__(TushareProvider)
    provider._client = lambda: Client()
    result = provider.dragon_tiger("600519.SH", date(2026, 8, 19), date(2026, 8, 20))

    assert [call["trade_date"] for call in calls] == ["20260819", "20260820"]
    assert all("start_date" not in call and "end_date" not in call for call in calls)
    assert len(result) == 1


def test_akshare_margin_uses_exchange_interface_and_filters_security() -> None:
    calls: list[str] = []

    class FakeAk:
        @staticmethod
        def stock_margin_detail_sse(*, date):
            calls.append(date)
            return pd.DataFrame(
                [
                    {"证券代码": "600519", "融资余额": 100, "融资买入额": 20},
                    {"证券代码": "600000", "融资余额": 999, "融资买入额": 1},
                ]
            )

    provider = object.__new__(AkshareProvider)
    provider._ak = lambda: FakeAk()
    result = provider.margin("600519.SH", date(2026, 8, 19), date(2026, 8, 20))

    assert calls == ["20260819", "20260820"]
    assert result["证券代码"].tolist() == ["600519", "600519"]
    assert result["trade_date"].tolist() == [date(2026, 8, 19), date(2026, 8, 20)]
