from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import requests

from config.settings import settings

logger = logging.getLogger(__name__)


class HttpClientError(RuntimeError):
    """统一 HTTP 客户端错误。"""


class HttpClient:
    """带超时、重试、退避和最小请求间隔的轻量 HTTP 客户端。"""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        min_interval_seconds: float = 0.2,
        session: requests.Session | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        self.timeout = timeout if timeout is not None else settings.request_timeout_seconds
        self.max_retries = max(0, max_retries if max_retries is not None else settings.request_max_retries)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn
        self._last_request_at = 0.0

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        attempts = self.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            self._wait_for_rate_limit()
            try:
                response = self.session.request(method, url, **kwargs)
                self._last_request_at = time.monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < attempts - 1:
                        self._sleep_before_retry(attempt, response.headers)
                        continue
                response.raise_for_status()
                return response
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    break
                self._sleep_before_retry(attempt, {})

        if last_error is not None:
            raise HttpClientError(f"HTTP 请求失败: {method} {url}: {last_error}") from last_error
        raise HttpClientError(f"HTTP 请求失败: {method} {url}")

    def json(self, method: str, url: str, **kwargs: Any) -> Any:
        return self.request(method, url, **kwargs).json()

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            self.sleep_fn(remaining)

    def _sleep_before_retry(self, attempt: int, headers: Mapping[str, str]) -> None:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                delay = min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = 2.0**attempt
        else:
            delay = min(30.0, 0.5 * (2**attempt))
        logger.warning("HTTP 请求将重试，第 %s 次，等待 %.1f 秒", attempt + 1, delay)
        self.sleep_fn(delay)
