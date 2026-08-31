from __future__ import annotations

from datetime import date
from typing import Any

from config.settings import settings
from .base import DataProvider, DataProviderError


class AkshareProvider(DataProvider):
    """AKShare 研究型适配器；部分接口依赖第三方公开数据源。"""

    name = "akshare"

    def index_daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        """获取 A 股指数日线；AKShare 使用 sh000001/sz399006 形式的代码。"""
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装 akshare") from exc

        code = symbol.upper().split(".", 1)[0]
        exchange = symbol.upper().rsplit(".", 1)[-1] if "." in symbol else ""
        ak_symbol = ("sh" if exchange in {"SH", "SSE"} or code.startswith("000") else "sz") + code
        return ak.stock_zh_index_daily(symbol=ak_symbol)

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

        code = symbol.upper().split(".", 1)[0]
        return ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            # 统一内部口径为未复权；前复权在技术分析层另行计算。
            adjust="",
        )

    def realtime_snapshot(self) -> Any:
        """获取研究型实时快照；生产环境应使用授权行情源。"""
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError("未安装 akshare") from exc
        return ak.stock_zh_a_spot_em()
