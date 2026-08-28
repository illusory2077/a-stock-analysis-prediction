from __future__ import annotations

from datetime import date
from typing import Any

from config.settings import settings
from .base import DataProvider, DataProviderError


class TushareProvider(DataProvider):
    """Tushare Pro 适配器骨架。具体接口调用集中在本文件。"""

    name = "tushare"

    def __init__(self, token: str | None = None) -> None:
        self.token = token or settings.tushare_token
        if not self.token:
            raise DataProviderError("未配置 TUSHARE_TOKEN")

    def _client(self) -> Any:
        import tushare as ts

        return ts.pro_api(self.token)

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
