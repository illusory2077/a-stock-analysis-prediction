from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from config.settings import settings
from .base import DataProvider, DataProviderError


class TushareProvider(DataProvider):
    """Tushare Pro 适配器；供应商调用集中在本文件。"""

    name = "tushare"
    data_version = "tushare-pro"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.tushare_token
        if not self.token:
            raise DataProviderError("未配置 TUSHARE_TOKEN")

    def _client(self) -> Any:
        import tushare as ts

        return ts.pro_api(self.token)

    def trade_calendar(self, start_date: date, end_date: date, *, exchange: str = "SSE") -> Any:
        client = self._client()
        return client.trade_cal(
            exchange=exchange,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields="exchange,cal_date,is_open,pretrade_date",
        )

    def index_daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        client = self._client()
        return client.index_daily(
            ts_code=symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        )

    def fund_flow(self, symbol: str, start_date: date, end_date: date) -> Any:
        client = self._client()
        return client.moneyflow(
            ts_code=symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            fields=(
                "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
                "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,"
                "sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,"
                "sell_elg_amount,net_mf_vol,net_mf_amount"
            ),
        )

    def margin(self, symbol: str, start_date: date, end_date: date) -> Any:
        """获取融资融券明细；Tushare 金额字段按供应商口径已是人民币元。"""
        client = self._client()
        return client.margin_detail(
            ts_code=symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )

    def dragon_tiger(self, symbol: str, start_date: date, end_date: date) -> Any:
        """按交易日调用 Tushare ``top_list``，避免向仅支持 trade_date 的接口传范围参数。"""
        client = self._client()
        frames: list[pd.DataFrame] = []
        current = start_date
        while current <= end_date:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            result = client.top_list(
                ts_code=symbol,
                trade_date=current.strftime("%Y%m%d"),
            )
            frame = result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
            if not frame.empty:
                frames.append(frame)
            current += timedelta(days=1)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)

    def healthcheck(self) -> dict[str, Any]:
        try:
            self._client()
            return {"provider": self.name, "configured": True, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "configured": True, "ok": False, "error": str(exc)}

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        client = self._client()
        return client.daily(
            ts_code=symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
