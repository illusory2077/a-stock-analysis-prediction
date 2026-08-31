"""A 股大盘环境评估。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

import pandas as pd


MARKET_ENVIRONMENT_RULES_VERSION = "1.0"
INDEX_LABELS = {
    "000001.SH": "上证指数",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
}


class MarketEnvironmentError(ValueError):
    """大盘环境输入不符合要求。"""


@dataclass(frozen=True)
class MarketEnvironmentResult:
    """可审计的大盘环境评分结果。"""

    score: float | None
    data_cutoff: str | None
    generated_at: str
    summary: dict[str, Any]

    def to_dimension(self) -> dict[str, Any]:
        """转换成次日预测层可消费的标准维度。"""
        return {
            "available": self.score is not None,
            "score": self.score,
            "evidence": self.summary.get("evidence", []),
            "warnings": self.summary.get("warnings", []),
            "data_cutoff": self.data_cutoff,
            "source": self.summary.get("source"),
            "rules_version": MARKET_ENVIRONMENT_RULES_VERSION,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_environment_rules_version": MARKET_ENVIRONMENT_RULES_VERSION,
            "score": self.score,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "summary": self.summary,
        }


def evaluate_market_environment(
    index_data: Mapping[str, pd.DataFrame],
    *,
    breadth: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> MarketEnvironmentResult:
    """根据指数日线和可选市场宽度，生成 -1 到 +1 的大盘环境评分。

    指数评分使用趋势（收盘价相对 MA20）和 5 日动量；市场宽度仅在传入
    真实的上涨/下跌家数时参与计算。缺失的指数或宽度不会被填充为中性值。
    不同指数最新日期不一致时，只使用共同截止日之前的数据，并保留警告。
    """
    if not isinstance(index_data, Mapping):
        raise MarketEnvironmentError("index_data 必须是映射类型")

    generated = _as_utc(generated_at or datetime.now(timezone.utc))
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    latest_dates: dict[str, date] = {}
    sources: set[str] = set()

    for raw_symbol, raw_frame in index_data.items():
        symbol = _canonical_symbol(raw_symbol)
        if not isinstance(raw_frame, pd.DataFrame):
            warnings.append(f"{symbol} 数据不是 pandas DataFrame，已跳过")
            continue
        frame = _prepare_frame(raw_frame)
        if frame.empty:
            warnings.append(f"{symbol} 没有有效指数收盘数据，已跳过")
            continue
        frames[symbol] = frame
        latest_dates[symbol] = frame.iloc[-1]["trade_date"]
        source_values = frame.get("source", pd.Series(dtype=object)).dropna().astype(str).unique()
        sources.update(source_values.tolist())

    if not frames:
        return _result(
            score=None,
            data_cutoff=None,
            generated=generated,
            summary={
                "status": "unavailable",
                "available": False,
                "indices": {},
                "breadth": {"available": False},
                "evidence": [],
                "warnings": [*warnings, "没有可用的指数行情，无法生成大盘环境评分"],
                "source": None,
            },
        )

    common_cutoff = min(latest_dates.values())
    if len(set(latest_dates.values())) > 1:
        warnings.append(
            "指数最新交易日不一致，已使用共同截止日 "
            f"{common_cutoff.isoformat()}，避免混用不同时间截面"
        )

    index_results: dict[str, dict[str, Any]] = {}
    index_scores: list[float] = []
    evidence: list[str] = []
    for symbol, frame in frames.items():
        clipped = frame.loc[frame["trade_date"] <= common_cutoff].copy()
        result = _score_index(symbol, clipped)
        index_results[symbol] = result
        if result["available"]:
            index_scores.append(float(result["score"]))
            evidence.extend(result["evidence"])
        warnings.extend(result["warnings"])

    breadth_result = _score_breadth(breadth)
    if breadth_result["available"]:
        evidence.extend(breadth_result["evidence"])

    components: list[tuple[float, float]] = []
    if index_scores:
        components.append((sum(index_scores) / len(index_scores), 0.75))
    if breadth_result["available"]:
        components.append((float(breadth_result["score"]), 0.25 if index_scores else 1.0))

    if not components:
        warnings.append("指数历史不足且市场宽度不可用，无法生成大盘环境评分")
        score = None
        status = "unavailable"
    else:
        weight_total = sum(weight for _, weight in components)
        score = round(max(-1.0, min(1.0, sum(value * weight for value, weight in components) / weight_total)), 6)
        status = "validated_with_warning" if warnings else "validated"

    if len(index_scores) < 2:
        warnings.append("可用指数少于 2 个，大盘环境覆盖不完整")
        status = "validated_with_warning" if score is not None else status

    return _result(
        score=score,
        data_cutoff=common_cutoff.isoformat(),
        generated=generated,
        summary={
            "status": status,
            "available": score is not None,
            "indices": index_results,
            "breadth": breadth_result,
            "index_score": round(sum(index_scores) / len(index_scores), 6) if index_scores else None,
            "evidence": list(dict.fromkeys(evidence)),
            "warnings": list(dict.fromkeys(warnings)),
            "source": ", ".join(sorted(sources)) if sources else None,
        },
    )


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "close"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.date
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["trade_date", "close"])
    result = result.loc[result["close"] > 0]
    return result.sort_values("trade_date", kind="stable").drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def _score_index(symbol: str, frame: pd.DataFrame) -> dict[str, Any]:
    label = INDEX_LABELS.get(symbol, symbol)
    if len(frame) < 6:
        return {
            "label": label,
            "available": False,
            "score": None,
            "latest_trade_date": frame.iloc[-1]["trade_date"].isoformat() if not frame.empty else None,
            "evidence": [],
            "warnings": [f"{label} 历史记录不足 6 个交易日，无法计算 5 日动量"],
        }

    close = frame["close"]
    latest_close = float(close.iloc[-1])
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    return_5d = latest_close / float(close.iloc[-6]) - 1.0
    trend_score: float | None = None
    if ma20 is not None and ma20 > 0:
        distance = latest_close / ma20 - 1.0
        trend_score = 1.0 if distance >= 0.005 else -1.0 if distance <= -0.005 else 0.0
    momentum_score = 1.0 if return_5d >= 0.02 else -1.0 if return_5d <= -0.02 else 0.0
    available_scores = [value for value in (trend_score, momentum_score) if value is not None]
    score = round(sum(available_scores) / len(available_scores), 6)
    evidence = [
        f"{label} 5日涨跌幅 {return_5d * 100:.2f}%",
        f"{label} 5日动量评分 {momentum_score:+.1f}",
    ]
    warnings: list[str] = []
    if trend_score is not None:
        evidence.append(f"{label} 收盘相对20日均线 {'偏强' if trend_score > 0 else '偏弱' if trend_score < 0 else '中性'}")
    else:
        warnings.append(f"{label} 历史不足 20 日，未使用长期趋势评分")
    return {
        "label": label,
        "available": True,
        "score": score,
        "latest_trade_date": frame.iloc[-1]["trade_date"].isoformat(),
        "latest_close": round(latest_close, 6),
        "return_5d": round(return_5d, 6),
        "ma20": round(ma20, 6) if ma20 is not None else None,
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "evidence": evidence,
        "warnings": warnings,
    }


def _score_breadth(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"available": False, "score": None, "evidence": [], "warnings": ["市场宽度未提供真实数据"]}
    if not isinstance(value, Mapping):
        raise MarketEnvironmentError("breadth 必须是映射类型")
    advancing = _number(value.get("advancing"))
    declining = _number(value.get("declining"))
    if advancing is None or declining is None or advancing < 0 or declining < 0 or advancing + declining <= 0:
        return {
            "available": False,
            "score": None,
            "evidence": [],
            "warnings": ["市场宽度缺少有效 advancing/declining，未参与评分"],
        }
    score = round(max(-1.0, min(1.0, (advancing - declining) / (advancing + declining))), 6)
    return {
        "available": True,
        "score": score,
        "advancing": int(advancing),
        "declining": int(declining),
        "evidence": [f"上涨家数 {int(advancing)}、下跌家数 {int(declining)}，市场宽度评分 {score:+.3f}"],
        "warnings": [],
    }


def _canonical_symbol(value: Any) -> str:
    text = str(value).strip().upper()
    if "." not in text and text.isdigit():
        text = f"{text}.SZ" if text.startswith("399") else f"{text}.SH"
    return text


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _result(*, score: float | None, data_cutoff: str | None, generated: datetime, summary: dict[str, Any]) -> MarketEnvironmentResult:
    return MarketEnvironmentResult(
        score=score,
        data_cutoff=data_cutoff,
        generated_at=generated.isoformat(),
        summary=summary,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
