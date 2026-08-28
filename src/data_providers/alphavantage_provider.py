from __future__ import annotations

from datetime import date
from typing import Any

from config.settings import settings
from .base import DataProvider, DataProviderError


class AlphaVantageProvider(DataProvider):
    """Alpha Vantage 低频全球行情适配器。"""

    name = "alphavantage"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.alphavantage_key
        if not self.api_key:
            raise DataProviderError("未配置 ALPHAVANTAGE_KEY")

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.api_key), "ok": bool(self.api_key)}

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        raise NotImplementedError("待接入 Alpha Vantage TIME_SERIES_DAILY，并统一字段")
