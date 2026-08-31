"""可解释的资金行为评分。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

FUND_FLOW_SCORE_RULES_VERSION = "1.0"


@dataclass(frozen=True)
class FundFlowResult:
    score: float | None
    data_cutoff: str | None
    generated_at: str
    summary: dict[str, Any]

    def to_dimension(self) -> dict[str, Any]:
        return {
            "available": self.score is not None,
            "score": self.score,
            "evidence": self.summary.get("evidence", []),
            "warnings": self.summary.get("warnings", []),
            "data_cutoff": self.data_cutoff,
            "source": self.summary.get("source"),
            "rules_version": FUND_FLOW_SCORE_RULES_VERSION,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_flow_score_rules_version": FUND_FLOW_SCORE_RULES_VERSION,
            "score": self.score,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "summary": self.summary,
        }


def evaluate_fund_flow(data: pd.DataFrame, *, generated_at: datetime | None = None) -> FundFlowResult:
    """根据主力净流入金额的强度和连续性生成 -1 到 +1 的评分。"""
    generated = _as_utc(generated_at or datetime.now(timezone.utc))
    if not isinstance(data, pd.DataFrame):
        raise ValueError("资金流向输入必须是 pandas DataFrame")
    required = {"trade_date", "net_flow_amount"}
    if not required.issubset(data.columns) or data.empty:
        return _unavailable(generated, "资金流向数据不足，无法评分")

    frame = data.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["net_flow_amount"] = pd.to_numeric(frame["net_flow_amount"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "net_flow_amount"]).sort_values("trade_date", kind="stable")
    frame = frame.drop_duplicates("trade_date", keep="last").reset_index(drop=True)
    if frame.empty:
        return _unavailable(generated, "资金流向没有有效交易日或净流入金额")

    amounts = frame["net_flow_amount"].astype(float)
    baseline = float(amounts.tail(20).abs().median())
    latest_amount = float(amounts.iloc[-1])
    intensity = math.tanh(latest_amount / baseline) if baseline > 0 else (1.0 if latest_amount > 0 else -1.0 if latest_amount < 0 else 0.0)
    directions = amounts.tail(5).map(lambda value: 1.0 if value > 0 else -1.0 if value < 0 else 0.0)
    consistency = float(directions.mean()) if len(directions) else 0.0
    score = round(max(-1.0, min(1.0, 0.65 * intensity + 0.35 * consistency)), 6)
    warnings: list[str] = []
    if len(frame) < 5:
        warnings.append("资金流向历史少于 5 个交易日，连续性评分参考有限")
    if baseline == 0:
        warnings.append("最近 20 日资金净流入基线为 0，强度评分退化为方向评分")
    evidence = [
        f"最新主力净流入 {latest_amount / 10000:+.2f} 万元",
        f"资金流强度评分 {intensity:+.3f}",
        f"近 {len(directions)} 日资金方向一致性 {consistency:+.3f}",
    ]
    source_values = frame.get("source", pd.Series(dtype=object)).dropna().astype(str).unique()
    source = ", ".join(sorted(source_values.tolist())) if len(source_values) else None
    cutoff = frame.iloc[-1]["trade_date"].isoformat()
    summary = {
        "status": "validated_with_warning" if warnings else "validated",
        "available": True,
        "latest_trade_date": cutoff,
        "latest_net_flow_amount": round(latest_amount, 6),
        "baseline_abs_median_20d": round(baseline, 6),
        "intensity_score": round(intensity, 6),
        "consistency_score": round(consistency, 6),
        "evidence": evidence,
        "warnings": warnings,
        "source": source,
    }
    return FundFlowResult(score=score, data_cutoff=cutoff, generated_at=generated.isoformat(), summary=summary)


def _unavailable(generated: datetime, warning: str) -> FundFlowResult:
    return FundFlowResult(
        score=None,
        data_cutoff=None,
        generated_at=generated.isoformat(),
        summary={"status": "unavailable", "available": False, "evidence": [], "warnings": [warning], "source": None},
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
