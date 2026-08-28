from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class DataProviderError(RuntimeError):
    """数据源不可用、鉴权失败或返回数据异常。"""


class DataProvider(ABC):
    """所有行情/基本面数据源适配器的最小接口。"""

    name: str

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        """检查配置和数据源可用性，返回可记录到日志的状态。"""
        raise NotImplementedError

    @abstractmethod
    def daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Any:
        """获取指定标的的日线数据，并转换为统一字段。"""
        raise NotImplementedError
