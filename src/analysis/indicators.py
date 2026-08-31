"""技术指标计算。

本模块只接受已经通过预测输入质量门禁的标准日线，所有滚动/指数计算
均按交易日期升序执行，避免把未来行带入较早日期的指标。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

import pandas as pd

from .quality_gate import QualityGateResult, require_prediction_input


DEFAULT_MA_WINDOWS = (5, 10, 20, 60)
DEFAULT_RSI_WINDOWS = (6, 14)
DEFAULT_BOLLINGER_WINDOW = 20
DEFAULT_BOLLINGER_STD = 2.0
DEFAULT_ATR_WINDOW = 14
DEFAULT_SUPPORT_RESISTANCE_WINDOWS = (20, 60)


class TechnicalIndicatorError(ValueError):
    """技术指标输入不符合要求。"""


@dataclass(frozen=True)
class TechnicalIndicatorResult:
    """技术指标和对应的输入门禁结果。"""

    data: pd.DataFrame
    quality_gate: QualityGateResult
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """返回不包含 DataFrame 的可序列化摘要。"""
        return {
            "quality_gate": self.quality_gate.to_dict(),
            "summary": self.summary,
        }


def calculate_technical_indicators(
    data: pd.DataFrame,
    quality_report: Mapping[str, Any] | None = None,
    *,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    retrieved_at: datetime | None = None,
    route_degraded: bool = False,
    ma_windows: tuple[int, ...] = DEFAULT_MA_WINDOWS,
    rsi_windows: tuple[int, ...] = DEFAULT_RSI_WINDOWS,
    bollinger_window: int = DEFAULT_BOLLINGER_WINDOW,
    bollinger_std: float = DEFAULT_BOLLINGER_STD,
    atr_window: int = DEFAULT_ATR_WINDOW,
    support_resistance_windows: tuple[int, ...] = DEFAULT_SUPPORT_RESISTANCE_WINDOWS,
) -> TechnicalIndicatorResult:
    """计算技术指标，并强制要求输入先通过质量门禁。

    ``degraded`` 数据允许计算，但结果会携带门禁告警；``blocked`` 数据会
    抛出 :class:`PredictionInputBlockedError`，不会产生指标结果。
    """
    gate = require_prediction_input(
        data,
        quality_report,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        retrieved_at=retrieved_at,
        route_degraded=route_degraded,
    )
    frame = _prepare_frame(data)
    _validate_parameters(
        ma_windows=ma_windows,
        rsi_windows=rsi_windows,
        bollinger_window=bollinger_window,
        bollinger_std=bollinger_std,
        atr_window=atr_window,
        support_resistance_windows=support_resistance_windows,
    )

    result = frame.copy()
    close = result["close"]
    high = result["high"]
    low = result["low"]
    indicator_columns: list[str] = []

    for window in ma_windows:
        name = f"ma_{window}"
        result[name] = close.rolling(window=window, min_periods=window).mean()
        indicator_columns.append(name)

    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    result["ema_12"] = ema_12
    result["ema_26"] = ema_26
    result["macd_dif"] = ema_12 - ema_26
    result["macd_dea"] = result["macd_dif"].ewm(span=9, adjust=False, min_periods=9).mean()
    # 国内行情软件通常将 MACD 柱表示为 2 * (DIF - DEA)。
    result["macd_hist"] = 2 * (result["macd_dif"] - result["macd_dea"])
    indicator_columns.extend(["ema_12", "ema_26", "macd_dif", "macd_dea", "macd_hist"])

    for window in rsi_windows:
        name = f"rsi_{window}"
        result[name] = _rsi(close, window)
        indicator_columns.append(name)

    bollinger_mid = close.rolling(window=bollinger_window, min_periods=bollinger_window).mean()
    bollinger_stddev = close.rolling(window=bollinger_window, min_periods=bollinger_window).std(ddof=0)
    result[f"bollinger_mid_{bollinger_window}"] = bollinger_mid
    result[f"bollinger_upper_{bollinger_window}"] = bollinger_mid + bollinger_std * bollinger_stddev
    result[f"bollinger_lower_{bollinger_window}"] = bollinger_mid - bollinger_std * bollinger_stddev
    result[f"bollinger_bandwidth_{bollinger_window}"] = _safe_divide(
        result[f"bollinger_upper_{bollinger_window}"] - result[f"bollinger_lower_{bollinger_window}"],
        bollinger_mid,
    )
    result[f"bollinger_percent_b_{bollinger_window}"] = _safe_divide(
        close - result[f"bollinger_lower_{bollinger_window}"],
        result[f"bollinger_upper_{bollinger_window}"] - result[f"bollinger_lower_{bollinger_window}"],
    )
    indicator_columns.extend(
        [
            f"bollinger_mid_{bollinger_window}",
            f"bollinger_upper_{bollinger_window}",
            f"bollinger_lower_{bollinger_window}",
            f"bollinger_bandwidth_{bollinger_window}",
            f"bollinger_percent_b_{bollinger_window}",
        ]
    )

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["true_range"] = true_range
    result[f"atr_{atr_window}"] = true_range.ewm(
        alpha=1 / atr_window,
        adjust=False,
        min_periods=atr_window,
    ).mean()
    indicator_columns.extend(["true_range", f"atr_{atr_window}"])

    for window in support_resistance_windows:
        support_name = f"support_{window}"
        resistance_name = f"resistance_{window}"
        result[support_name] = low.rolling(window=window, min_periods=1).min()
        result[resistance_name] = high.rolling(window=window, min_periods=1).max()
        indicator_columns.extend([support_name, resistance_name])

    result["return_1d"] = close.pct_change()
    result["volatility_20"] = result["return_1d"].rolling(window=20, min_periods=20).std(ddof=0)
    indicator_columns.extend(["return_1d", "volatility_20"])

    warnings = list(gate.warnings)
    history_rows = len(result)
    if history_rows < max((*ma_windows, *rsi_windows, bollinger_window, atr_window, *support_resistance_windows)):
        warnings.append("历史记录不足以完整计算全部长周期指标，早期指标值将为空")
    summary = {
        "symbol": symbol or _single_symbol(result),
        "history_rows": history_rows,
        "latest_trade_date": gate.data_cutoff,
        "indicator_columns": indicator_columns,
        "available_counts": {column: int(result[column].notna().sum()) for column in indicator_columns},
        "warnings": list(dict.fromkeys(warnings)),
    }
    result.attrs = {
        "quality_gate": gate.to_dict(),
        "indicator_summary": summary,
    }
    return TechnicalIndicatorResult(data=result, quality_gate=gate, summary=summary)


def _prepare_frame(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame):
        raise TechnicalIndicatorError("技术指标输入必须是 pandas DataFrame")
    frame = data.copy()
    required = {"trade_date", "open", "high", "low", "close"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise TechnicalIndicatorError(f"技术指标输入缺少字段: {', '.join(missing)}")
    parsed_dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    if parsed_dates.isna().any():
        raise TechnicalIndicatorError("技术指标输入包含无法解析的 trade_date")
    frame["trade_date"] = parsed_dates.dt.date
    for field in ("open", "high", "low", "close"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if frame[field].isna().any():
            raise TechnicalIndicatorError(f"技术指标输入包含无效 {field}")
    frame = frame.sort_values(["trade_date"], kind="stable").reset_index(drop=True)
    frame.attrs = {}
    return frame


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    average_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = average_gain.div(average_loss.where(average_loss != 0))
    rsi = 100 - (100 / (1 + relative_strength))
    no_loss = average_loss == 0
    rsi = rsi.mask(no_loss & (average_gain > 0), 100.0)
    rsi = rsi.mask(no_loss & (average_gain == 0), 50.0)
    return rsi.clip(lower=0, upper=100)


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.where(denominator != 0))


def _single_symbol(frame: pd.DataFrame) -> str | None:
    if "symbol" not in frame.columns or frame["symbol"].dropna().empty:
        return None
    return str(frame["symbol"].dropna().iloc[0])


def _validate_parameters(
    *,
    ma_windows: tuple[int, ...],
    rsi_windows: tuple[int, ...],
    bollinger_window: int,
    bollinger_std: float,
    atr_window: int,
    support_resistance_windows: tuple[int, ...],
) -> None:
    windows = (*ma_windows, *rsi_windows, bollinger_window, atr_window, *support_resistance_windows)
    if not windows or any(not isinstance(window, int) or window <= 0 for window in windows):
        raise TechnicalIndicatorError("指标窗口必须是正整数")
    if len(set(ma_windows)) != len(ma_windows) or len(set(rsi_windows)) != len(rsi_windows):
        raise TechnicalIndicatorError("均线和 RSI 窗口不能重复")
    if not isinstance(bollinger_std, (int, float)) or bollinger_std <= 0:
        raise TechnicalIndicatorError("布林带标准差倍数必须为正数")
