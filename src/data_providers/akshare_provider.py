from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from .base import DataProvider, DataProviderError


class AkshareProvider(DataProvider):
    """AKShare 研究型适配器；接口依赖公开数据源，不能默认视为生产级实时源。"""

    name = "akshare"
    data_version = "akshare"

    def _ak(self) -> Any:
        try:
            import akshare as ak

            return ak
        except ImportError as exc:
            raise DataProviderError("未安装 akshare") from exc

    def index_daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        ak = self._ak()
        code = symbol.upper().split(".", 1)[0]
        exchange = symbol.upper().rsplit(".", 1)[-1] if "." in symbol else ""
        ak_symbol = ("sh" if exchange in {"SH", "SSE"} or code.startswith("000") else "sz") + code
        return ak.stock_zh_index_daily(symbol=ak_symbol)

    def fund_flow(self, symbol: str, start_date: date, end_date: date) -> Any:
        ak = self._ak()
        code = symbol.upper().split(".", 1)[0]
        exchange = symbol.upper().rsplit(".", 1)[-1] if "." in symbol else "SH"
        market = {"SH": "sh", "SSE": "sh", "SZ": "sz", "SZSE": "sz", "BJ": "bj", "BSE": "bj"}.get(exchange, "sh")
        return ak.stock_individual_fund_flow(stock=code, market=market)

    def margin(self, symbol: str, start_date: date, end_date: date) -> Any:
        """按交易日调用交易所明细接口，只保留请求证券。"""
        ak = self._ak()
        exchange = symbol.upper().rsplit(".", 1)[-1] if "." in symbol else "SH"
        interface_name = {
            "SH": "stock_margin_detail_sse",
            "SSE": "stock_margin_detail_sse",
            "SZ": "stock_margin_detail_szse",
            "SZSE": "stock_margin_detail_szse",
            "BJ": "stock_margin_detail_bse",
            "BSE": "stock_margin_detail_bse",
        }.get(exchange)
        if not interface_name:
            raise DataProviderError(f"AKShare 暂不支持 {exchange} 市场融资融券明细")
        method = getattr(ak, interface_name, None)
        if not callable(method):
            raise DataProviderError(f"当前 AKShare 版本没有 {interface_name} 接口")

        code = symbol.upper().split(".", 1)[0].zfill(6)
        frames: list[pd.DataFrame] = []
        current = start_date
        while current <= end_date:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            query_date = current.strftime("%Y%m%d")
            result = method(date=query_date)
            frame = result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
            if not frame.empty:
                frame = self._filter_security(frame, code, interface_name)
                if "trade_date" not in frame.columns and "日期" not in frame.columns and "交易日期" not in frame.columns:
                    frame.insert(0, "trade_date", current)
                frames.append(frame)
            current += timedelta(days=1)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)

    def dragon_tiger(self, symbol: str, start_date: date, end_date: date) -> Any:
        ak = self._ak()
        code = symbol.upper().split(".", 1)[0].zfill(6)
        method = getattr(ak, "stock_lhb_detail_em", None)
        if not callable(method):
            raise DataProviderError("当前 AKShare 版本没有 stock_lhb_detail_em 接口")
        try:
            result = method(
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )
        except TypeError:
            result = method(start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))
        frame = result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
        if frame.empty:
            return frame
        return self._filter_security(frame, code, "stock_lhb_detail_em", allow_missing=True)

    @staticmethod
    def _filter_security(frame: pd.DataFrame, code: str, interface_name: str, *, allow_missing: bool = False) -> pd.DataFrame:
        candidates = ("symbol", "ts_code", "SECURITY_CODE", "代码", "股票代码", "证券代码")
        for column in candidates:
            if column not in frame.columns:
                continue
            values = frame[column].astype(str).str.extract(r"(\d{6})", expand=False)
            return frame.loc[values.eq(code)].copy()
        if allow_missing:
            return frame
        raise DataProviderError(f"{interface_name} 返回结果缺少证券代码字段，拒绝将全市场数据误归属到 {code}")

    def healthcheck(self) -> dict[str, Any]:
        try:
            self._ak()
            return {"provider": self.name, "configured": True, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "configured": False, "ok": False, "error": str(exc)}

    def realtime_snapshot(self) -> Any:
        """获取研究型实时快照；生产环境应使用授权行情源。"""
        return self._ak().stock_zh_a_spot_em()

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        ak = self._ak()
        code = symbol.upper().split(".", 1)[0]
        return ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
