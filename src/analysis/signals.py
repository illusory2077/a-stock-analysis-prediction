"""可解释技术信号层。

本模块把已经计算完成的技术指标转换成逐交易日、可审阅的技术信号。
信号只使用当前行及上一交易日的指标，不读取未来记录；输入必须携带
已通过质量门禁的 :class:`TechnicalIndicatorResult`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .indicators import TechnicalIndicatorResult
from .quality_gate import PredictionInputBlockedError, QualityGateResult


class TechnicalSignalError(ValueError):
    """技术信号输入不符合要求。"""


@dataclass(frozen=True)
class TechnicalSignalResult:
    """逐日技术信号及最新交易日摘要。"""

    data: pd.DataFrame
    quality_gate: QualityGateResult
    summary: dict[str, Any]

    @property
    def latest(self) -> dict[str, Any]:
        """返回最新一行信号的可序列化字典。"""
        if self.data.empty:
            return {}
        return _jsonable_record(self.data.iloc[-1].to_dict())

    def to_dict(self) -> dict[str, Any]:
        """返回不包含 DataFrame 的摘要。"""
        return {
            "quality_gate": self.quality_gate.to_dict(),
            "summary": self.summary,
            "latest": self.latest,
        }


def generate_technical_signals(
    indicator_result: TechnicalIndicatorResult,
    *,
    symbol: str | None = None,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    volatility_medium: float = 0.02,
    volatility_high: float = 0.04,
) -> TechnicalSignalResult:
    """根据技术指标生成可解释的趋势、动量和风险信号。

    综合分数由四个方向性组件构成：均线趋势（-2 到 +2）、MACD（-2 到 +2）、
    RSI（-1 到 +1）和布林带（-1 到 +1）。分数 ``>= 2`` 为 ``bullish``，
    ``<= -2`` 为 ``bearish``，其余为 ``neutral``。支撑/压力和波动率用于
    风险说明，不直接改变方向分数。
    """
    if not isinstance(indicator_result, TechnicalIndicatorResult):
        raise TechnicalSignalError("技术信号输入必须是 TechnicalIndicatorResult")
    if indicator_result.quality_gate.status == "blocked":
        raise PredictionInputBlockedError(indicator_result.quality_gate)
    _validate_thresholds(
        rsi_overbought=rsi_overbought,
        rsi_oversold=rsi_oversold,
        volatility_medium=volatility_medium,
        volatility_high=volatility_high,
    )

    frame = indicator_result.data.copy()
    if not isinstance(frame, pd.DataFrame):
        raise TechnicalSignalError("技术指标结果 data 必须是 pandas DataFrame")
    required = {
        "trade_date",
        "close",
        "ma_5",
        "ma_20",
        "ma_60",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "rsi_14",
        "bollinger_mid_20",
        "bollinger_upper_20",
        "bollinger_lower_20",
        "bollinger_percent_b_20",
        "support_20",
        "resistance_20",
        "volatility_20",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise TechnicalSignalError(f"技术指标结果缺少信号所需字段: {', '.join(missing)}")

    frame = frame.sort_values("trade_date", kind="stable").reset_index(drop=True)
    signal_rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        previous = frame.iloc[index - 1] if index else None
        trend = _trend_signal(row)
        macd = _macd_signal(row, previous)
        rsi = _rsi_signal(row, rsi_overbought, rsi_oversold)
        bollinger = _bollinger_signal(row)
        volatility = _volatility_signal(row, volatility_medium, volatility_high)
        levels = _level_context(row)

        components = {
            "trend": trend,
            "macd": macd,
            "rsi": rsi,
            "bollinger": bollinger,
        }
        valid_components = [item for item in components.values() if item["score"] is not None]
        score = sum(item["score"] for item in valid_components)
        maximum = sum(item["max_score"] for item in valid_components)
        direction = _direction(score, valid_components)
        strength = round(abs(score) / maximum * 100, 2) if maximum else 0.0
        strength_label = _strength_label(strength)
        confidence = _confidence(
            strength,
            len(valid_components),
            len(components),
            indicator_result.quality_gate.status,
        )

        triggers = _trigger_conditions(direction, trend, macd, rsi, bollinger, levels)
        invalidations = _invalidation_conditions(direction, levels, volatility)
        signal_rows.append(
            {
                "trend_signal": trend["label"],
                "trend_score": trend["score"],
                "macd_signal": macd["label"],
                "macd_score": macd["score"],
                "rsi_signal": rsi["label"],
                "rsi_score": rsi["score"],
                "bollinger_signal": bollinger["label"],
                "bollinger_score": bollinger["score"],
                "volatility_level": volatility["label"],
                "support_resistance_context": levels["label"],
                "support_distance_pct": levels["support_distance_pct"],
                "resistance_distance_pct": levels["resistance_distance_pct"],
                "composite_score": score if maximum else None,
                "signal_direction": direction,
                "signal_strength": strength,
                "signal_strength_label": strength_label,
                "signal_confidence": confidence,
                "signal_triggers": "；".join(triggers),
                "signal_invalidations": "；".join(invalidations),
            }
        )

    signal_frame = pd.concat([frame.reset_index(drop=True), pd.DataFrame(signal_rows)], axis=1)
    latest = signal_rows[-1] if signal_rows else {}
    warnings = list(indicator_result.summary.get("warnings", []))
    if not signal_rows:
        warnings.append("没有可生成技术信号的行情记录")
    if latest and latest.get("signal_direction") == "neutral":
        warnings.append("技术指标方向未形成一致信号，综合判断为 neutral")
    if latest and latest.get("volatility_level") == "high":
        warnings.append("20 日波动率处于高位，价格区间和信号置信度存在较大不确定性")
    warnings = list(dict.fromkeys(warnings))

    summary = {
        "symbol": symbol or indicator_result.summary.get("symbol"),
        "latest_trade_date": _as_text(frame.iloc[-1].get("trade_date")) if not frame.empty else None,
        "latest_close": _number(frame.iloc[-1].get("close")) if not frame.empty else None,
        "direction": latest.get("signal_direction") if latest else "neutral",
        "composite_score": latest.get("composite_score") if latest else None,
        "signal_strength": latest.get("signal_strength") if latest else 0.0,
        "signal_strength_label": latest.get("signal_strength_label") if latest else "weak",
        "confidence": latest.get("signal_confidence") if latest else 0.0,
        "components": {
            key: {"label": value["label"], "score": value["score"], "evidence": value["evidence"]}
            for key, value in (
                ("trend", _trend_signal(frame.iloc[-1]) if not frame.empty else _missing_component()),
                ("macd", _macd_signal(frame.iloc[-1], frame.iloc[-2] if len(frame) > 1 else None) if not frame.empty else _missing_component()),
                ("rsi", _rsi_signal(frame.iloc[-1], rsi_overbought, rsi_oversold) if not frame.empty else _missing_component()),
                ("bollinger", _bollinger_signal(frame.iloc[-1]) if not frame.empty else _missing_component()),
            )
        },
        "volatility": _volatility_summary(frame.iloc[-1], volatility_medium, volatility_high) if not frame.empty else {},
        "levels": _level_summary(frame.iloc[-1]) if not frame.empty else {},
        "triggers": _as_text_list(latest.get("signal_triggers")) if latest else [],
        "invalidations": _as_text_list(latest.get("signal_invalidations")) if latest else [],
        "warnings": warnings,
    }
    signal_frame.attrs = {
        "quality_gate": indicator_result.quality_gate.to_dict(),
        "signal_summary": summary,
    }
    return TechnicalSignalResult(data=signal_frame, quality_gate=indicator_result.quality_gate, summary=summary)


def _trend_signal(row: pd.Series) -> dict[str, Any]:
    values = [_number(row.get(name)) for name in ("ma_5", "ma_20", "ma_60")]
    if any(value is None for value in values):
        return _component("insufficient_data", None, "MA5/MA20/MA60 历史数据不足", 2)
    ma5, ma20, ma60 = values
    if ma5 > ma20 > ma60:
        return _component("bullish_alignment", 2, "MA5 > MA20 > MA60，上升排列", 2)
    if ma5 < ma20 < ma60:
        return _component("bearish_alignment", -2, "MA5 < MA20 < MA60，下降排列", 2)
    if ma5 > ma20:
        return _component("short_term_bullish", 1, "MA5 位于 MA20 上方，但中长期均线未完全多头排列", 2)
    if ma5 < ma20:
        return _component("short_term_bearish", -1, "MA5 位于 MA20 下方，但中长期均线未完全空头排列", 2)
    return _component("mixed", 0, "均线方向混合", 2)


def _macd_signal(row: pd.Series, previous: pd.Series | None) -> dict[str, Any]:
    dif, dea, hist = (_number(row.get(name)) for name in ("macd_dif", "macd_dea", "macd_hist"))
    if dif is None or dea is None or hist is None:
        return _component("insufficient_data", None, "MACD 历史数据不足", 2)
    prev_diff = _number(previous.get("macd_dif")) if previous is not None else None
    prev_dea = _number(previous.get("macd_dea")) if previous is not None else None
    if prev_diff is not None and prev_dea is not None and prev_diff <= prev_dea < dif:
        return _component("bullish_crossover", 2, "DIF 向上突破 DEA，形成金叉", 2)
    if prev_diff is not None and prev_dea is not None and prev_diff >= prev_dea > dif:
        return _component("bearish_crossover", -2, "DIF 向下跌破 DEA，形成死叉", 2)
    if dif > dea and hist > 0:
        return _component("bullish_momentum", 1, "DIF 在 DEA 上方且 MACD 柱为正", 2)
    if dif < dea and hist < 0:
        return _component("bearish_momentum", -1, "DIF 在 DEA 下方且 MACD 柱为负", 2)
    return _component("mixed", 0, "MACD 方向混合", 2)


def _rsi_signal(row: pd.Series, overbought: float, oversold: float) -> dict[str, Any]:
    value = _number(row.get("rsi_14"))
    if value is None:
        return _component("insufficient_data", None, "RSI14 历史数据不足", 1)
    if value <= oversold:
        return _component("oversold", 1, f"RSI14={value:.2f}，低于或等于超卖阈值 {oversold:g}", 1)
    if value >= overbought:
        return _component("overbought", -1, f"RSI14={value:.2f}，高于或等于超买阈值 {overbought:g}", 1)
    if value >= 50:
        return _component("positive", 0, f"RSI14={value:.2f}，处于中性偏强区间", 1)
    return _component("negative", 0, f"RSI14={value:.2f}，处于中性偏弱区间", 1)


def _bollinger_signal(row: pd.Series) -> dict[str, Any]:
    percent_b = _number(row.get("bollinger_percent_b_20"))
    close = _number(row.get("close"))
    middle = _number(row.get("bollinger_mid_20"))
    if percent_b is None or close is None or middle is None:
        return _component("insufficient_data", None, "布林带历史数据不足", 1)
    if percent_b >= 1:
        return _component("upper_breakout", 1, "收盘价位于布林带上轨或上方，动量偏强但存在过热风险", 1)
    if percent_b <= 0:
        return _component("lower_breakdown", -1, "收盘价位于布林带下轨或下方，动量偏弱", 1)
    if close > middle:
        return _component("above_middle", 1, "收盘价位于布林带中轨上方", 1)
    if close < middle:
        return _component("below_middle", -1, "收盘价位于布林带中轨下方", 1)
    return _component("at_middle", 0, "收盘价接近布林带中轨", 1)


def _volatility_signal(row: pd.Series, medium: float, high: float) -> dict[str, Any]:
    value = _number(row.get("volatility_20"))
    if value is None:
        return {"label": "insufficient_data", "value": None, "evidence": "20 日波动率历史数据不足"}
    if value >= high:
        label = "high"
    elif value >= medium:
        label = "medium"
    else:
        label = "low"
    return {"label": label, "value": value, "evidence": f"20 日波动率={value:.4%}"}


def _level_context(row: pd.Series) -> dict[str, Any]:
    close = _number(row.get("close"))
    support = _number(row.get("support_20"))
    resistance = _number(row.get("resistance_20"))
    if close is None:
        return {"label": "insufficient_data", "support_distance_pct": None, "resistance_distance_pct": None}
    support_distance = (close - support) / close * 100 if support not in (None, 0) else None
    resistance_distance = (resistance - close) / close * 100 if resistance is not None and close != 0 else None
    if support_distance is not None and support_distance <= 2:
        label = "near_support"
    elif resistance_distance is not None and resistance_distance <= 2:
        label = "near_resistance"
    else:
        label = "between_levels"
    return {
        "label": label,
        "support_distance_pct": round(support_distance, 4) if support_distance is not None else None,
        "resistance_distance_pct": round(resistance_distance, 4) if resistance_distance is not None else None,
    }


def _direction(score: int, components: list[dict[str, Any]]) -> str:
    if not components:
        return "neutral"
    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _strength_label(value: float) -> str:
    if value >= 60:
        return "strong"
    if value >= 30:
        return "moderate"
    return "weak"


def _confidence(strength: float, available: int, total: int, gate_status: str) -> float:
    if not available:
        return 0.0
    confidence = 0.5 * (available / total) + 0.5 * (strength / 100)
    if gate_status == "degraded":
        confidence *= 0.85
    return round(min(max(confidence, 0.0), 1.0), 3)


def _trigger_conditions(direction: str, trend: dict[str, Any], macd: dict[str, Any], rsi: dict[str, Any], bollinger: dict[str, Any], levels: dict[str, Any]) -> list[str]:
    conditions: list[str] = []
    if direction == "bullish":
        conditions.append("收盘价维持在关键短中期均线和支撑位上方")
        if trend["score"] and trend["score"] > 0:
            conditions.append(trend["evidence"])
        if macd["score"] and macd["score"] > 0:
            conditions.append(macd["evidence"])
    elif direction == "bearish":
        conditions.append("收盘价跌破关键支撑位或短中期均线继续向下")
        if trend["score"] and trend["score"] < 0:
            conditions.append(trend["evidence"])
        if macd["score"] and macd["score"] < 0:
            conditions.append(macd["evidence"])
    else:
        conditions.append("等待均线、MACD 或布林带方向形成一致信号")
    if levels["label"] == "near_resistance":
        conditions.append("突破近端压力位后，偏多信号才更可靠")
    if levels["label"] == "near_support":
        conditions.append("支撑位有效企稳后，反弹信号才更可靠")
    return list(dict.fromkeys(conditions))


def _invalidation_conditions(direction: str, levels: dict[str, Any], volatility: dict[str, Any]) -> list[str]:
    conditions: list[str] = []
    if direction == "bullish":
        conditions.append("收盘价有效跌破20日支撑位")
        conditions.append("MACD 死叉且均线多头排列被破坏")
    elif direction == "bearish":
        conditions.append("收盘价重新站上20日压力位")
        conditions.append("MACD 金叉且均线空头排列被破坏")
    else:
        conditions.append("均线、MACD 与布林带出现同向突破")
    if volatility["label"] == "high":
        conditions.append("高波动环境下单日大幅跳空或涨跌停会使技术信号失真")
    return conditions


def _component(label: str, score: int | None, evidence: str, max_score: int) -> dict[str, Any]:
    return {"label": label, "score": score, "evidence": evidence, "max_score": max_score}


def _missing_component() -> dict[str, Any]:
    return _component("insufficient_data", None, "无数据", 1)


def _volatility_summary(row: pd.Series, medium: float, high: float) -> dict[str, Any]:
    signal = _volatility_signal(row, medium, high)
    return {"level": signal["label"], "value": signal["value"], "evidence": signal["evidence"]}


def _level_summary(row: pd.Series) -> dict[str, Any]:
    context = _level_context(row)
    return {
        "context": context["label"],
        "support_20": _number(row.get("support_20")),
        "resistance_20": _number(row.get("resistance_20")),
        "support_distance_pct": context["support_distance_pct"],
        "resistance_distance_pct": context["resistance_distance_pct"],
    }


def _validate_thresholds(*, rsi_overbought: float, rsi_oversold: float, volatility_medium: float, volatility_high: float) -> None:
    if not 0 <= rsi_oversold < rsi_overbought <= 100:
        raise TechnicalSignalError("RSI 阈值必须满足 0 <= 超卖 < 超买 <= 100")
    if not 0 <= volatility_medium < volatility_high:
        raise TechnicalSignalError("波动率阈值必须满足 0 <= 中等 < 高波动")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _as_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)


def _as_text_list(value: Any) -> list[str]:
    if not value:
        return []
    return [part for part in str(value).split("；") if part]


def _jsonable_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(key): (_number(value) if isinstance(value, (float, int)) else _as_text(value) if pd.isna(value) else value) for key, value in record.items()}
