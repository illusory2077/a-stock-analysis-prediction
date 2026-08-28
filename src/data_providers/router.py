from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from config.settings import settings
from .akshare_provider import AkshareProvider
from .base import DataProvider, DataProviderError
from .tavily_provider import TavilyProvider
from .tushare_provider import TushareProvider


class DataSourceRouter:
    """按优先级调用数据源，并在可重试失败后自动降级。"""

    def __init__(
        self,
        *,
        market_providers: Iterable[DataProvider] | None = None,
        news_providers: Iterable[Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.market_providers = list(market_providers) if market_providers is not None else self._default_market_providers()
        self.news_providers = list(news_providers) if news_providers is not None else self._default_news_providers()
        self.sleep_fn = sleep_fn

    def fetch_daily_bars(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取日线，并返回数据、来源、是否降级和尝试记录。"""
        return self._run_with_fallback(
            self.market_providers,
            lambda provider: provider.daily_bars(symbol, start_date, end_date),
            operation_name=f"获取 {symbol} 日线",
        )

    def search_news(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """按新闻数据源优先级搜索，返回原始响应及路由元数据。"""
        return self._run_with_fallback(
            self.news_providers,
            lambda provider: provider.search_news(query, **kwargs),
            operation_name=f"搜索新闻: {query}",
        )

    def healthcheck(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for provider in [*self.market_providers, *self.news_providers]:
            try:
                checks.append(provider.healthcheck())
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    {
                        "provider": getattr(provider, "name", type(provider).__name__),
                        "configured": False,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return checks

    def _run_with_fallback(
        self,
        providers: list[Any],
        operation: Callable[[Any], Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        if not providers:
            raise DataProviderError(f"没有可用的数据源: {operation_name}")

        attempts: list[dict[str, Any]] = []
        for index, provider in enumerate(providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                data, retries = self._call_with_retry(lambda: operation(provider))
                if self._is_empty_result(data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                return {
                    "data": data,
                    "source": provider_name,
                    "degraded": index > 0,
                    "attempts": [*attempts, {"provider": provider_name, "ok": True, "retries": retries}],
                }
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    {
                        "provider": provider_name,
                        "ok": False,
                        "error": str(exc),
                        "retryable": self._is_retryable(exc),
                    }
                )

        details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
        raise DataProviderError(f"{operation_name} 的所有数据源均失败: {details}")

    def _call_with_retry(self, operation: Callable[[], Any]) -> tuple[Any, int]:
        max_attempts = max(1, settings.request_max_retries + 1)
        for attempt in range(max_attempts):
            try:
                return operation(), attempt
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_attempts - 1 or not self._is_retryable(exc):
                    raise
                self.sleep_fn(min(10.0, 0.5 * (2**attempt)))
        raise RuntimeError("unreachable")

    @staticmethod
    def _is_empty_result(data: Any) -> bool:
        if data is None:
            return True
        if hasattr(data, "empty"):
            return bool(data.empty)
        if isinstance(data, (list, tuple, set, str)):
            return len(data) == 0
        if isinstance(data, dict):
            if "results" in data:
                return not data.get("results")
            return len(data) == 0
        return False

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            "429",
            "rate limit",
            "too many",
            "频率",
            "频次",
            "timeout",
            "timed out",
            "connection",
            "temporarily",
            "remote end closed",
            "503",
            "502",
            "504",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _default_market_providers() -> list[DataProvider]:
        providers: list[DataProvider] = []
        for provider_cls in (TushareProvider, AkshareProvider):
            try:
                providers.append(provider_cls())
            except DataProviderError:
                continue
        return providers

    @staticmethod
    def _default_news_providers() -> list[Any]:
        try:
            return [TavilyProvider()]
        except DataProviderError:
            return []
