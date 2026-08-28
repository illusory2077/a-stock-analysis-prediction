from __future__ import annotations

from datetime import date
from typing import Any

from config.settings import settings
from .base import DataProvider, DataProviderError


class AkshareProvider(DataProvider):
    """AKShare 研究型适配器；部分接口依赖第三方公开数据源。"""

    name = "akshare"

    def healthcheck(self) -> dict[str, Any]:
        try:
            import akshare  # noqa: F401

            return {"provider": self.name, "configured": True, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "configured": False, "ok": False, "error": str(exc)}

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装 akshare") from exc

        return ak.stock_zh_a_hist(
            symbol=symbol.replace(".SH", "").replace(".SZ", ""),
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )

    def realtime_snapshot(self) -> Any:
        """获取研究型实时快照；生产环境应使用授权行情源。"""
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装 akshare") from exc
        return ak.stock_zh_a_spot_em()
