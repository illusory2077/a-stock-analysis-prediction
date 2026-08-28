from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from config.settings import settings
from .base import DataProvider, DataProviderError


class TavilyProvider(DataProvider):
    """Tavily 新闻搜索和网页正文提取适配器。"""

    name = "tavily"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.tavily_api_key
        if not self.api_key:
            raise DataProviderError("未配置 TAVILY_API_KEY")
        self._client_instance: Any | None = None

    @property
    def client(self) -> Any:
        if self._client_instance is None:
            try:
                from tavily import TavilyClient
            except ImportError as exc:
                raise DataProviderError("未安装 tavily-python，请先安装 requirements.txt") from exc
            self._client_instance = TavilyClient(api_key=self.api_key)
        return self._client_instance

    def healthcheck(self) -> dict[str, Any]:
        try:
            _ = self.client
            return {"provider": self.name, "configured": True, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "configured": bool(self.api_key), "ok": False, "error": str(exc)}

    def search_news(
        self,
        query: str,
        *,
        time_range: str | None = "week",
        max_results: int = 10,
        include_domains: Iterable[str] | None = None,
        exclude_domains: Iterable[str] | None = None,
        search_depth: str = "basic",
        include_raw_content: bool = False,
    ) -> dict[str, Any]:
        """搜索新闻；返回原始 Tavily 响应，保留来源和相关性信息。"""
        if not query.strip():
            raise ValueError("query 不能为空")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results 必须在 1 到 20 之间")

        kwargs: dict[str, Any] = {
            "topic": "news",
            "time_range": time_range,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": include_raw_content,
        }
        if include_domains:
            kwargs["include_domains"] = list(include_domains)
        if exclude_domains:
            kwargs["exclude_domains"] = list(exclude_domains)
        return self.client.search(query=query, **kwargs)

    def extract(self, urls: Iterable[str]) -> dict[str, Any]:
        """提取指定网页正文；仅对已筛选的少量 URL 调用。"""
        url_list = [url for url in urls if url.strip()]
        if not url_list:
            raise ValueError("urls 不能为空")
        return self.client.extract(urls=url_list)

    def daily_bars(self, symbol: str, start_date: date, end_date: date) -> Any:
        raise NotImplementedError("Tavily 用于新闻和网页，不提供行情日线")
