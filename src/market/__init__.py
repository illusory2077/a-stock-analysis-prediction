"""行情标准化、质量检查、交易日历和交叉验证。"""

from .calendar import CalendarError, CalendarValidationResult, TradingCalendar, validate_observed_trade_dates
from .cross_validator import compare_daily_bars
from .fund_flow import (
    FUND_FLOW_COLUMNS,
    FUND_FLOW_RULES_VERSION,
    FundFlowNormalizationError,
    FundFlowQualityResult,
    compare_fund_flow,
    normalize_fund_flow,
    validate_fund_flow,
)
from .normalizer import MarketNormalizationError, normalize_daily_bars
from .quality import QualityResult, validate_daily_bars
from .schema import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, STANDARD_COLUMNS

__all__ = [
    "CalendarError",
    "FUND_FLOW_COLUMNS",
    "FUND_FLOW_RULES_VERSION",
    "FundFlowNormalizationError",
    "FundFlowQualityResult",
    "CalendarValidationResult",
    "MarketNormalizationError",
    "OPTIONAL_COLUMNS",
    "QualityResult",
    "REQUIRED_COLUMNS",
    "STANDARD_COLUMNS",
    "TradingCalendar",
    "compare_daily_bars",
    "compare_fund_flow",
    "normalize_daily_bars",
    "normalize_fund_flow",
    "validate_daily_bars",
    "validate_fund_flow",
    "validate_observed_trade_dates",
]
