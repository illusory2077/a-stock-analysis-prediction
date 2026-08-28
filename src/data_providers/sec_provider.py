from __future__ import annotations

from typing import Any

from config.settings import settings
from src.utils.http_client import HttpClient
from .base import DataProvider, DataProviderError


class SecEdgarProvider(DataProvider):
    """SEC EDGAR 公共数据适配器；使用 User-Agent，不需要 API Key。"""

    name = "sec_edgar"
    endpoint = "https://data.sec.gov"

    def __init__(self, user_agent: str | None = None) -> None:
        self.user_agent = user_agent or settings.sec_user_agent
        if not self.user_agent or "your_email@example.com" in self.user_agent:
            raise DataProviderError("请在 SEC_USER_AGENT 中填写真实联系邮箱")
        self.http = HttpClient()

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": True, "ok": True}

    def get_json(self, path: str) -> Any:
        return self.http.get(
            f"{self.endpoint.rstrip('/')}/{path.lstrip('/')}",
            headers={"User-Agent": self.user_agent},
        ).json()

    def daily_bars(self, symbol: str, start_date: Any, end_date: Any) -> Any:
        raise NotImplementedError("SEC EDGAR 用于财报/申报数据，不提供股票行情日线")
