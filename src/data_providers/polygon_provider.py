from __future__ import annotations

from datetime import date
from typing import Any

from config.settings import settings
from .base import DataProvider, DataProviderError


class PolygonProvider(DataProvider):
    """Polygon 美股行情适配器骨架；实时流应单独实现 WebSocket 客户端。"""

    name = "polygon"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.polygon_api_key
        if not self.api_key:
            raise DataProviderError("未配置 POLYGON_API_KEY")

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.api_key), "ok": bool(self.api_key)}

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        raise NotImplementedError("待接入 Polygon REST 历史行情，并统一字段")

    def realtime_stream(self) -> Any:
        raise NotImplementedError("待接入 Polygon WebSocket 实时行情")
