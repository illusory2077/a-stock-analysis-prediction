"""行情标准化、质量检查、交易日历和交叉验证。"""

from .calendar import CalendarError, CalendarValidationResult, TradingCalendar, validate_observed_trade_dates
from .cross_validator import compare_daily_bars
from .dragon_tiger import (
    DRAGON_TIGER_COLUMNS,
    DRAGON_TIGER_RULES_VERSION,
    DragonTigerNormalizationError,
    DragonTigerQualityResult,
    compare_dragon_tiger,
    normalize_dragon_tiger,
    validate_dragon_tiger,
)
from .fund_flow import (
    FUND_FLOW_COLUMNS,
    FUND_FLOW_RULES_VERSION,
    FundFlowNormalizationError,
    FundFlowQualityResult,
    compare_fund_flow,
    normalize_fund_flow,
    validate_fund_flow,
)
from .margin import (
    MARGIN_COLUMNS,
    MARGIN_RULES_VERSION,
    MarginNormalizationError,
    MarginQualityResult,
    compare_margin,
    normalize_margin,
    validate_margin,
)
from .normalizer import MarketNormalizationError, normalize_daily_bars
from .quality import QualityResult, validate_daily_bars
from .schema import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, STANDARD_COLUMNS

__all__ = [
    "CalendarError", "FUND_FLOW_COLUMNS", "FUND_FLOW_RULES_VERSION", "FundFlowNormalizationError", "FundFlowQualityResult",
    "DRAGON_TIGER_COLUMNS", "DRAGON_TIGER_RULES_VERSION", "DragonTigerNormalizationError", "DragonTigerQualityResult",
    "MARGIN_COLUMNS", "MARGIN_RULES_VERSION", "MarginNormalizationError", "MarginQualityResult",
    "CalendarValidationResult", "MarketNormalizationError", "OPTIONAL_COLUMNS", "QualityResult", "REQUIRED_COLUMNS", "STANDARD_COLUMNS", "TradingCalendar",
    "compare_daily_bars", "compare_dragon_tiger", "compare_fund_flow", "compare_margin", "normalize_daily_bars", "normalize_dragon_tiger", "normalize_fund_flow", "normalize_margin", "validate_daily_bars", "validate_dragon_tiger", "validate_fund_flow", "validate_margin", "validate_observed_trade_dates",
]
