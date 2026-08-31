"""可解释的资金行为评分，包含主力资金流向及可选融资融券、龙虎榜补充证据。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

FUND_FLOW_SCORE_RULES_VERSION = "1.2"


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


def evaluate_fund_flow(
    data: pd.DataFrame,
    *,
    margin_data: pd.DataFrame | None = None,
    dragon_tiger_data: pd.DataFrame | None = None,
    generated_at: datetime | None = None,
) -> FundFlowResult:
    """评分范围为 -1 到 +1；补充数据仅参与资金行为维度，不新增预测维度。"""
    generated = _as_utc(generated_at or datetime.now(timezone.utc))
    warnings: list[str] = []
    evidence: list[str] = []
    components: dict[str, Any] = {}

    base_score, base_meta, base_evidence, base_warnings = _evaluate_base(data)
    components["fund_flow"] = base_meta
    evidence.extend(base_evidence)
    warnings.extend(base_warnings)

    margin_score, margin_meta, margin_evidence, margin_warnings = _evaluate_margin(margin_data)
    dragon_score, dragon_meta, dragon_evidence, dragon_warnings = _evaluate_dragon_tiger(dragon_tiger_data)
    components["margin"] = margin_meta
    components["dragon_tiger"] = dragon_meta
    evidence.extend(margin_evidence)
    evidence.extend(dragon_evidence)
    warnings.extend(margin_warnings)
    warnings.extend(dragon_warnings)

    available: list[tuple[str, float, float]] = []
    if base_score is not None:
        available.append(("fund_flow", base_score, 0.60))
    if margin_score is not None:
        available.append(("margin", margin_score, 0.20))
    if dragon_score is not None:
        available.append(("dragon_tiger", dragon_score, 0.20))
    if not available:
        return _unavailable(generated, warnings or ["资金行为数据不足，无法评分"])

    weight_total = sum(weight for _, _, weight in available)
    score = round(_clip(sum(value * weight for _, value, weight in available) / weight_total), 6)
    cutoffs: list[date] = []
    if base_meta.get("latest_trade_date"):
        cutoffs.append(date.fromisoformat(base_meta["latest_trade_date"]))
    if margin_meta.get("latest_trade_date"):
        cutoffs.append(date.fromisoformat(margin_meta["latest_trade_date"]))
    if dragon_meta.get("latest_trade_date"):
        cutoffs.append(date.fromisoformat(dragon_meta["latest_trade_date"]))
    cutoff = max(cutoffs).isoformat() if cutoffs else None

    source_values: set[str] = set()
    for frame in (data, margin_data, dragon_tiger_data):
        if isinstance(frame, pd.DataFrame) and "source" in frame.columns:
            source_values.update(frame["source"].dropna().astype(str))

    summary = {
        "status": "validated_with_warning" if warnings else "validated",
        "available": True,
        "latest_trade_date": cutoff,
        "latest_net_flow_amount": base_meta.get("latest_net_flow_amount"),
        "baseline_abs_median_20d": base_meta.get("baseline_abs_median_20d"),
        "intensity_score": base_meta.get("intensity_score"),
        "consistency_score": base_meta.get("consistency_score"),
        "evidence": list(dict.fromkeys(evidence)),
        "warnings": list(dict.fromkeys(warnings)),
        "source": ",".join(sorted(source_values)) or None,
        "components": components,
        "subscores": {name: value for name, value, _ in available},
        "weights": {name: weight for name, _, weight in available},
    }
    return FundFlowResult(score=score, data_cutoff=cutoff, generated_at=generated.isoformat(), summary=summary)


def _evaluate_base(data: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str], list[str]]:
    frame = _prepare_base(data)
    if frame is None or frame.empty:
        return None, {"available": False, "score": None}, [], ["资金流向数据不足，跳过基础资金流评分"]

    amounts = frame["net_flow_amount"].astype(float)
    baseline = float(amounts.tail(20).abs().median())
    latest_amount = float(amounts.iloc[-1])
    if baseline > 0:
        intensity = math.tanh(latest_amount / baseline)
    else:
        intensity = 1.0 if latest_amount > 0 else -1.0 if latest_amount < 0 else 0.0
    directions = amounts.tail(5).map(lambda value: 1.0 if value > 0 else -1.0 if value < 0 else 0.0)
    consistency = float(directions.mean()) if len(directions) else 0.0
    score = _clip(0.65 * intensity + 0.35 * consistency)

    warnings: list[str] = []
    if len(frame) < 5:
        warnings.append("资金流向历史少于 5 个交易日，连续性评分参考有限")
    if baseline == 0:
        warnings.append("最近 20 日资金净流入基线为 0，强度评分退化为方向评分")
    meta = {
        "available": True,
        "score": round(score, 6),
        "latest_trade_date": frame.iloc[-1]["trade_date"].isoformat(),
        "latest_net_flow_amount": round(latest_amount, 6),
        "intensity_score": round(intensity, 6),
        "consistency_score": round(consistency, 6),
        "baseline_abs_median_20d": round(baseline, 6),
    }
    evidence = [
        f"最新主力净流入 {latest_amount / 10000:+.2f} 万元",
        f"资金流强度评分 {intensity:+.3f}",
        f"近 {len(directions)} 日资金方向一致性 {consistency:+.3f}",
    ]
    return score, meta, evidence, warnings


def _prepare_base(data: pd.DataFrame | None) -> pd.DataFrame | None:
    if not isinstance(data, pd.DataFrame) or data.empty or "trade_date" not in data.columns:
        return None
    frame = data.copy()
    if "net_flow_amount" not in frame.columns:
        return None
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["net_flow_amount"] = pd.to_numeric(frame["net_flow_amount"], errors="coerce")
    frame = (
        frame.dropna(subset=["trade_date", "net_flow_amount"])
        .sort_values("trade_date", kind="stable")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    return frame if not frame.empty else None


def _evaluate_margin(data: pd.DataFrame | None) -> tuple[float | None, dict[str, Any], list[str], list[str]]:
    unavailable = {"available": False, "score": None}
    if not isinstance(data, pd.DataFrame) or data.empty or "trade_date" not in data.columns:
        return None, unavailable, [], ["融资融券数据不可用，未纳入资金行为评分"]

    frame = data.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    for field in ("margin_balance", "short_balance"):
        if field in frame.columns:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        else:
            frame[field] = float("nan")
    frame = frame.dropna(subset=["trade_date"]).sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    if frame.empty or not any(frame[field].notna().any() for field in ("margin_balance", "short_balance")):
        return None, unavailable, [], ["融资融券没有有效余额字段，未纳入评分"]

    sub_scores: list[tuple[str, float, float]] = []
    evidence: list[str] = []
    warnings: list[str] = []
    latest = frame.iloc[-1]["trade_date"]
    for field, weight, direction, label in (
        ("margin_balance", 0.65, 1.0, "融资余额"),
        ("short_balance", 0.35, -1.0, "融券余额"),
    ):
        values = frame[field].dropna()
        if len(values) < 2:
            continue
        change = float(values.iloc[-1] - values.iloc[-2])
        scale = float(values.diff().abs().tail(20).median())
        if scale > 0:
            raw_score = math.tanh(change / scale)
        else:
            raw_score = 1.0 if change > 0 else -1.0 if change < 0 else 0.0
        score = _clip(direction * raw_score)
        sub_scores.append((field, score, weight))
        evidence.append(f"{label}日变动 {change / 10000:+.2f} 万元" + ("（上升偏空）" if direction < 0 else ""))

    if not sub_scores:
        return None, unavailable, [], ["融资融券只有单日余额，未生成余额变化信号"]
    if len(sub_scores) < 2:
        warnings.append("融资融券仅有一个余额序列具备连续数据，已按可用子项重新归一化")
    total_weight = sum(weight for _, _, weight in sub_scores)
    score = _clip(sum(value * weight for _, value, weight in sub_scores) / total_weight)
    meta = {
        "available": True,
        "score": round(score, 6),
        "latest_trade_date": latest.isoformat(),
        "available_subscores": [name for name, _, _ in sub_scores],
    }
    return score, meta, evidence, warnings


def _evaluate_dragon_tiger(data: pd.DataFrame | None) -> tuple[float | None, dict[str, Any], list[str], list[str]]:
    unavailable = {"available": False, "score": None}
    if not isinstance(data, pd.DataFrame) or data.empty or "trade_date" not in data.columns:
        return None, unavailable, [], ["龙虎榜数据不可用，未纳入资金行为评分"]

    frame = data.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    if "net_buy_amount" not in frame.columns:
        frame["net_buy_amount"] = float("nan")
    frame["net_buy_amount"] = pd.to_numeric(frame["net_buy_amount"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).sort_values("trade_date")
    valid = frame.dropna(subset=["net_buy_amount"])
    if valid.empty:
        return None, unavailable, [], ["龙虎榜没有有效净买入额，未纳入评分"]

    daily = valid.groupby("trade_date", as_index=False)["net_buy_amount"].sum()
    latest_date = daily.iloc[-1]["trade_date"]
    latest = float(daily.iloc[-1]["net_buy_amount"])
    baseline = float(daily["net_buy_amount"].tail(20).abs().median())
    score = math.tanh(latest / baseline) if baseline > 0 else (1.0 if latest > 0 else -1.0 if latest < 0 else 0.0)
    evidence = [f"龙虎榜最新净买入 {latest / 10000:+.2f} 万元"]

    warnings: list[str] = []
    if "institution_net_amount" in valid.columns:
        institution = pd.to_numeric(valid["institution_net_amount"], errors="coerce")
        latest_institution = institution[valid["trade_date"].eq(latest_date)].dropna()
        if not latest_institution.empty:
            evidence.append(f"机构净买入合计 {float(latest_institution.sum()) / 10000:+.2f} 万元")
        else:
            warnings.append("龙虎榜机构净买入字段在最新上榜日不可用")
    else:
        warnings.append("龙虎榜未提供机构净买入字段，仅使用龙虎榜净买入")

    meta = {
        "available": True,
        "score": round(_clip(score), 6),
        "latest_trade_date": latest_date.isoformat(),
        "latest_net_buy_amount": latest,
        "baseline_abs_median_20d": baseline,
    }
    return _clip(score), meta, evidence, warnings


def _unavailable(generated: datetime, warnings: list[str]) -> FundFlowResult:
    return FundFlowResult(
        score=None,
        data_cutoff=None,
        generated_at=generated.isoformat(),
        summary={
            "status": "unavailable",
            "available": False,
            "evidence": [],
            "warnings": list(dict.fromkeys(warnings)),
            "source": None,
            "components": {},
        },
    )


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
