from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """项目运行配置；密钥从环境变量或项目根目录 .env 读取。"""

    project_root: Path
    app_env: str
    log_level: str
    data_timezone: str
    request_timeout_seconds: int
    request_max_retries: int
    tushare_token: str
    fred_api_key: str
    alphavantage_key: str
    tavily_api_key: str
    polygon_api_key: str
    sec_user_agent: str
    market_cross_validate: bool = True
    market_validate_calendar: bool = True
    market_close_diff_threshold: float = 0.005
    market_volume_diff_threshold: float = 0.02
    market_amount_diff_threshold: float = 0.02

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    def has_tushare(self) -> bool:
        return bool(self.tushare_token)

    def has_fred(self) -> bool:
        return bool(self.fred_api_key)

    def has_alphavantage(self) -> bool:
        return bool(self.alphavantage_key)

    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key)

    def has_polygon(self) -> bool:
        return bool(self.polygon_api_key)


def _load_dotenv(path: Path) -> None:
    """轻量加载 .env；已存在的系统环境变量优先。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值，支持 true/false、1/0、yes/no")


def _env_ratio(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是非负数字") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} 必须是非负有限数字")
    return value


_load_dotenv(PROJECT_ROOT / ".env")


settings = Settings(
    project_root=PROJECT_ROOT,
    app_env=os.getenv("APP_ENV", "development"),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    data_timezone=os.getenv("DATA_TIMEZONE", "Asia/Shanghai"),
    request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
    request_max_retries=int(os.getenv("REQUEST_MAX_RETRIES", "3")),
    market_cross_validate=_env_bool("MARKET_CROSS_VALIDATE", True),
    market_validate_calendar=_env_bool("MARKET_VALIDATE_CALENDAR", True),
    market_close_diff_threshold=_env_ratio("MARKET_CLOSE_DIFF_THRESHOLD", 0.005),
    market_volume_diff_threshold=_env_ratio("MARKET_VOLUME_DIFF_THRESHOLD", 0.02),
    market_amount_diff_threshold=_env_ratio("MARKET_AMOUNT_DIFF_THRESHOLD", 0.02),
    tushare_token=os.getenv("TUSHARE_TOKEN", ""),
    fred_api_key=os.getenv("FRED_API_KEY", ""),
    alphavantage_key=os.getenv("ALPHAVANTAGE_KEY", ""),
    tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
    polygon_api_key=os.getenv("POLYGON_API_KEY", ""),
    sec_user_agent=os.getenv("SEC_USER_AGENT", "AStockAnalysis your_email@example.com"),
)
