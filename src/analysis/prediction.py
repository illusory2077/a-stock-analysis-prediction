"""A 股次日预测模块。

该模块提供一个可审计的四维加权框架：大盘环境 30%、资金行为 30%、
技术面 25%、消息面 15%。当前项目已有可用的技术信号输入；其余维度
在未接入真实数据时明确标记为 unavailable，不用默认值冒充真实判断，
并对概率和置信度施加覆盖率限制。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from typing import Any, Mapping

import pandas as pd

from .quality_gate import PredictionInputBlockedError, QualityGateResult
from .signals import TechnicalSignalResult


PREDICTION_RULES_VERSION = "1.0"
DIMENSION_WEIGHTS = {
    "market_environment": 0.30,
    "fund_flow": 0.30,
    "technical": 0.25,
    "news": 0.15,
}


class NextDayPredictionError(ValueError):
    """次日预测输入不符合要求。"""


@dataclass(frozen=True)
class NextDayPredictionResult:
    """次日预测结果及其质量、证据和限制。"""

    symbol: str | None
    data_cutoff: str | None
    target_trade_date: str | None
    generated_at: str
    quality_gate: QualityGateResult
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_rules_version": PREDICTION_RULES_VERSION,
            "symbol": self.symbol,
            "data_cutoff": self.data_cutoff,
            "target_trade_date": self.target_trade_date,
            "generated_at": self.generated_at,
            "quality_gate": self.quality_gate.to_dict(),
            "summary": self.summary,
        }


def generate_next_day_prediction(
    technical_signals: TechnicalSignalResult,
    *,
    symbol: str | None = None,
    market_environment: Mapping[str, Any] | None = None,
    fund_flow: Mapping[str, Any] | None = None,
    news: Mapping[str, Any] | None = None,
    target_trade_date: date | None = None,
    generated_at: datetime | None = None,
) -> NextDayPredictionResult:
    """根据四维输入生成下一交易日的方向概率和价格区间。

    可选维度映射至少支持 ``score``（-1 到 +1），并可带有
    ``evidence``、``warnings`` 和 ``available``。未提供的维度会被标记为
    unavailable，并从有效权重分母中剔除；因此输出是“当前数据覆盖率下的
    条件性估计”，而非完整模型结论。
    """
    if not isinstance(technical_signals, TechnicalSignalResult):
        raise NextDayPredictionError("技术预测输入必须是 TechnicalSignalResult")
    if technical_signals.quality_gate.status == "blocked" or not technical_signals.quality_gate.can_predict:
        raise PredictionInputBlockedError(technical_signals.quality_gate)

    latest = _latest_row(technical_signals)
    close = _number(latest.get("close"))
    atr = _number(latest.get("atr_14"))
    volatility = _number(latest.get("volatility_20"))
    if close is None or close <= 0:
        raise NextDayPredictionError("技术信号缺少有效最新收盘价")
    if atr is None and volatility is None:
        raise NextDayPredictionError("技术信号缺少 ATR14 或 20 日波动率，无法估计价格区间")

    assessments = {
        "market_environment": _coerce_dimension("market_environment", market_environment),
        "fund_flow": _coerce_dimension("fund_flow", fund_flow),
        "technical": _technical_dimension(technical_signals),
        "news": _coerce_dimension("news", news),
    }
    available = {name: item for name, item in assessments.items() if item["available"]}
    if not available:
        raise NextDayPredictionError("没有可用的预测维度")

    available_weight = sum(DIMENSION_WEIGHTS[name] for name in available)
    combined_score = sum(
        DIMENSION_WEIGHTS[name] * item["score"] for name, item in available.items()
    ) / available_weight
    direction = _direction(combined_score)
    signal_strength = abs(combined_score)
    coverage = available_weight
    confidence = _confidence(
        coverage=coverage,
        signal_strength=signal_strength,
        gate_status=technical_signals.quality_gate.status,
    )
    probabilities = _probabilities(combined_score, confidence)
    move_pct = _move_pct(close, atr, volatility)
    expected_return_pct = round(combined_score * move_pct * 0.5 * 100, 4)
    center = close * (1 + expected_return_pct / 100)
    price_range = {
        "lower": round(center * (1 - move_pct), 4),
        "upper": round(center * (1 + move_pct), 4),
        "unit": "price",
        "method": "latest_close ± max(1.5×ATR14, 1.65×20日波动率, 1%)",
    }

    warnings = list(technical_signals.summary.get("warnings", []))
    missing = [name for name in DIMENSION_WEIGHTS if name not in available]
    if missing:
        warnings.append(f"预测维度未接入真实数据: {', '.join(missing)}")
        warnings.append("当前结果为技术面覆盖下的条件性估计，不应视为完整四维预测")
    if technical_signals.quality_gate.status == "degraded":
        warnings.append("行情质量门禁为 degraded，预测置信度已下调")
    for item in assessments.values():
        warnings.extend(item["warnings"])
    warnings = list(dict.fromkeys(warnings))

    triggers = list(technical_signals.summary.get("triggers", []))
    invalidations = list(technical_signals.summary.get("invalidations", []))
    if direction == "bullish":
        triggers.append("大盘、资金和消息面不得出现与技术面相反的强负面证据")
    elif direction == "bearish":
        triggers.append("大盘、资金和消息面不得出现与技术面相反的强正面证据")
    else:
        triggers.append("等待多个预测维度形成同向证据")
    invalidations.append("预测数据截止时间之后出现的新公告、突发新闻或交易状态变化不在本结果覆盖范围内")

    generated = _as_utc(generated_at or datetime.now(timezone.utc))
    symbol_value = symbol or technical_signals.summary.get("symbol")
    cutoff = technical_signals.summary.get("latest_trade_date")
    summary = {
        "forecast_horizon": "next_trading_day",
        "direction": direction,
        "probabilities": probabilities,
        "confidence": confidence,
        "signal_strength": round(signal_strength, 4),
        "coverage": round(coverage, 4),
        "available_weight": round(available_weight, 4),
        "latest_close": round(close, 4),
        "expected_return_pct": expected_return_pct,
        "price_range": price_range,
        "components": _component_summary(assessments, available_weight),
        "missing_dimensions": missing,
        "triggers": list(dict.fromkeys(triggers)),
        "invalidations": list(dict.fromkeys(invalidations)),
        "warnings": warnings,
    }
    return NextDayPredictionResult(
        symbol=str(symbol_value) if symbol_value is not None else None,
        data_cutoff=str(cutoff) if cutoff is not None else None,
        target_trade_date=target_trade_date.isoformat() if target_trade_date else None,
        generated_at=generated.isoformat(),
        quality_gate=technical_signals.quality_gate,
        summary=summary,
    )


def _technical_dimension(result: TechnicalSignalResult) -> dict[str, Any]:
    summary = result.summary
    score = _number(summary.get("composite_score"))
    if score is None:
        return _dimension("technical", None, False, ["技术综合分数不可用"])
    # 四个方向组件的理论最大绝对分数为 6：趋势 2、MACD 2、RSI 1、布林带 1。
    normalized = max(-1.0, min(1.0, score / 6.0))
    evidence = [
        f"技术方向={summary.get('direction', 'neutral')}",
        f"技术综合分数={score:g}",
    ]
    for name, item in summary.get("components", {}).items():
        if isinstance(item, Mapping) and item.get("evidence"):
            evidence.append(f"{name}: {item['evidence']}")
    return _dimension("technical", normalized, True, evidence, summary.get("warnings", []))


def _coerce_dimension(name: str, value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return _dimension(name, None, False, [f"{name} 未提供真实数据"])
    if not isinstance(value, Mapping):
        raise NextDayPredictionError(f"{name} 必须是映射类型")
    available = bool(value.get("available", True))
    score = _number(value.get("score"))
    if available and score is None:
        raise NextDayPredictionError(f"{name} 已标记可用但缺少 score")
    if score is not None and not -1 <= score <= 1:
        raise NextDayPredictionError(f"{name}.score 必须在 -1 到 1 之间")
    evidence = _text_list(value.get("evidence"))
    warnings = _text_list(value.get("warnings"))
    if not evidence:
        evidence = [f"{name} 已提供标准化评分"] if available else [f"{name} 不可用"]
    return _dimension(name, score, available and score is not None, evidence, warnings)


def _dimension(
    name: str,
    score: float | None,
    available: bool,
    evidence: list[str],
    warnings: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "score": round(score, 6) if score is not None else None,
        "available": available,
        "evidence": list(dict.fromkeys(evidence)),
        "warnings": list(dict.fromkeys(warnings or [])),
    }


def _component_summary(assessments: dict[str, dict[str, Any]], available_weight: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, item in assessments.items():
        base = DIMENSION_WEIGHTS[name]
        result[name] = {
            "base_weight": base,
            "effective_weight": round(base / available_weight, 6) if item["available"] else 0.0,
            "available": item["available"],
            "score": item["score"],
            "evidence": item["evidence"],
            "warnings": item["warnings"],
        }
    return result


def _latest_row(result: TechnicalSignalResult) -> dict[str, Any]:
    if result.data.empty:
        raise NextDayPredictionError("技术信号为空，无法预测")
    row = result.data.iloc[-1]
    return {str(key): value for key, value in row.to_dict().items()}


def _direction(score: float) -> str:
    if score >= 0.15:
        return "bullish"
    if score <= -0.15:
        return "bearish"
    return "neutral"


def _confidence(*, coverage: float, signal_strength: float, gate_status: str) -> float:
    gate_factor = 0.75 if gate_status == "degraded" else 1.0
    value = (0.25 * coverage + 0.45 * signal_strength + 0.30 * gate_factor) * gate_factor
    if coverage < 0.50:
        value = min(value, 0.40)
    elif coverage < 0.75:
        value = min(value, 0.65)
    return round(min(max(value, 0.0), 0.95), 3)


def _probabilities(score: float, confidence: float) -> dict[str, float]:
    scale = 3.0
    logits = [score * scale, 0.0, -score * scale]
    maximum = max(logits)
    exp_values = [math.exp(value - maximum) for value in logits]
    total = sum(exp_values)
    base = [value / total for value in exp_values]
    uniform = 1 / 3
    probabilities = [uniform + confidence * (value - uniform) for value in base]
    rounded = [round(value, 4) for value in probabilities]
    # 先四舍五入再把尾差归入 bullish，确保展示值严格合计为 1。
    rounded[0] = round(rounded[0] + (1.0 - sum(rounded)), 4)
    return {
        "bullish": rounded[0],
        "neutral": rounded[1],
        "bearish": rounded[2],
    }


def _move_pct(close: float, atr: float | None, volatility: float | None) -> float:
    candidates = [0.01]
    if atr is not None and atr >= 0:
        candidates.append(1.5 * atr / close)
    if volatility is not None and volatility >= 0:
        candidates.append(1.65 * volatility)
    return min(max(candidates), 0.15)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
