from __future__ import annotations

from typing import Any

from config.settings import settings
from src.utils.http_client import HttpClient
from .base import DataProvider, DataProviderError


class FredProvider(DataProvider):
    """FRED 宏观数据适配器。"""

    name = "fred"
    endpoint = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.fred_api_key
        if not self.api_key:
            raise DataProviderError("未配置 FRED_API_KEY")
        self.http = HttpClient()

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.api_key), "ok": bool(self.api_key)}

    def series_observations(self, series_id: str, **kwargs: Any) -> Any:
        params = {
            "api_key": self.api_key,
            "file_type": "json",
            "series_id": series_id,
            **kwargs,
        }
        return self.http.get(self.endpoint, params=params).json()

    def daily_bars(self, symbol: str, start_date: Any, end_date: Any) -> Any:
        """宏观序列不是真正的股票日线；保留接口以便统一调度，推荐调用 series_observations。"""
        return self.series_observations(
            symbol,
            observation_start=start_date.isoformat(),
            observation_end=end_date.isoformat(),
        )
