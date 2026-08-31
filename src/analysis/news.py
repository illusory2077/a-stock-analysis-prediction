"""可审计的消息面评分与信息截面控制。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.news.normalizer import filter_news_by_cutoff

NEWS_SCORE_RULES_VERSION = "1.0"

_POSITIVE_TERMS = (
    "利好", "增持", "回购", "中标", "订单", "增长", "超预期", "盈利", "上调",
    "获批", "获准", "支持", "突破", "上涨", "反弹", "改善", "扩产", "签约",
    "positive", "beat", "growth", "upgrade", "approval", "buyback", "contract",
)
_NEGATIVE_TERMS = (
    "利空", "减持", "处罚", "诉讼", "亏损", "下滑", "下降", "暴雷", "立案", "监管",
    "风险", "违约", "退市", "被调查", "问询", "下调", "跌停", "裁员", "事故",
    "negative", "miss", "downgrade", "lawsuit", "default", "investigation",
)


@dataclass(frozen=True)
class NewsResult:
    """消息面评分、截面筛选和可复核证据。"""

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
            "rules_version": NEWS_SCORE_RULES_VERSION,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "news_score_rules_version": NEWS_SCORE_RULES_VERSION,
            "score": self.score,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "summary": self.summary,
        }


def evaluate_news(
    items: Iterable[Mapping[str, Any]],
    *,
    cutoff: date | datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
    generated_at: datetime | None = None,
) -> NewsResult:
    """根据已发布新闻的透明关键词信号生成 -1 到 +1 的消息面评分。

    评分只接受信息截面之前、且具有可解析 ``published_at`` 的新闻。
    该规则是可解释的基线，不把 Tavily 相关性分数当成情绪分数。
    """
    generated = _as_utc(generated_at or datetime.now(timezone.utc))
    source_items = [dict(item) for item in items if isinstance(item, Mapping)]
    cutoff_report: dict[str, Any] = {}
    excluded: list[dict[str, Any]] = []
    if cutoff is not None:
        cutoff_result = filter_news_by_cutoff(source_items, cutoff=cutoff, timezone_name=timezone_name)
        eligible = cutoff_result.eligible
        excluded = cutoff_result.excluded
        cutoff_report = cutoff_result.report
    else:
        eligible = source_items

    if not eligible:
        warnings = ["没有具备有效发布时间且不晚于信息截面的新闻，消息面不可用"]
        warnings.extend(cutoff_report.get("warnings", []))
        return _unavailable(
            generated,
            warnings=warnings,
            cutoff_report=cutoff_report,
            excluded_count=len(excluded),
        )

    details: list[dict[str, Any]] = []
    weighted_scores: list[tuple[float, float]] = []
    positive_total = 0
    negative_total = 0
    for item in eligible:
        title = _text(item.get("title"))
        body = _text(item.get("summary")) + " " + _text(item.get("content"))
        text = f"{title} {body}".lower()
        positive = [term for term in _POSITIVE_TERMS if term.lower() in text]
        negative = [term for term in _NEGATIVE_TERMS if term.lower() in text]
        positive_total += len(positive)
        negative_total += len(negative)
        raw = len(positive) - len(negative)
        item_score = math.tanh(raw / 2.0)
        relevance = _bounded_float(item.get("score"), default=0.5)
        weight = 0.5 + 0.5 * relevance
        details.append(
            {
                "news_id": item.get("news_id"),
                "title": title,
                "published_at": item.get("published_at"),
                "sentiment_score": round(item_score, 6),
                "positive_terms": positive,
                "negative_terms": negative,
                "weight": round(weight, 6),
            }
        )
        weighted_scores.append((item_score, weight))

    total_weight = sum(weight for _, weight in weighted_scores)
    score = round(
        max(-1.0, min(1.0, sum(value * weight for value, weight in weighted_scores) / total_weight)),
        6,
    )
    warnings = list(cutoff_report.get("warnings", []))
    if positive_total == 0 and negative_total == 0:
        warnings.append("已纳入新闻未识别到预设利好/利空关键词，消息面评分接近中性")
    if len(eligible) < 3:
        warnings.append("有效新闻少于 3 条，消息面评分参考有限")

    cutoff_date = _latest_published_date(eligible, timezone_name)
    sources = sorted({str(item.get("source")) for item in eligible if item.get("source")})
    evidence = [
        f"有效新闻 {len(eligible)} 条，排除 {len(excluded)} 条",
        f"识别到利好关键词 {positive_total} 个、利空关键词 {negative_total} 个",
        f"消息面综合评分 {score:+.3f}",
    ]
    summary = {
        "status": "validated_with_warning" if warnings else "validated",
        "available": True,
        "score": score,
        "eligible_count": len(eligible),
        "excluded_count": len(excluded),
        "positive_term_count": positive_total,
        "negative_term_count": negative_total,
        "source": ", ".join(sources) or None,
        "cutoff": cutoff_report.get("cutoff"),
        "cutoff_report": cutoff_report,
        "evidence": evidence,
        "warnings": list(dict.fromkeys(warnings)),
        "items": details,
    }
    return NewsResult(score=score, data_cutoff=cutoff_date, generated_at=generated.isoformat(), summary=summary)


def _unavailable(
    generated: datetime,
    *,
    warnings: list[str],
    cutoff_report: dict[str, Any],
    excluded_count: int,
) -> NewsResult:
    return NewsResult(
        score=None,
        data_cutoff=None,
        generated_at=generated.isoformat(),
        summary={
            "status": "unavailable",
            "available": False,
            "eligible_count": 0,
            "excluded_count": excluded_count,
            "cutoff_report": cutoff_report,
            "evidence": [],
            "warnings": list(dict.fromkeys(warnings)),
            "source": None,
            "items": [],
        },
    )


def _latest_published_date(items: list[dict[str, Any]], timezone_name: str) -> str | None:
    tz = ZoneInfo(timezone_name)
    values: list[datetime] = []
    for item in items:
        raw = item.get("published_at_utc") or item.get("published_at")
        parsed = _parse_datetime(raw, tz)
        if parsed is not None:
            values.append(parsed.astimezone(tz))
    return max(values).date().isoformat() if values else None


def _parse_datetime(value: Any, default_tz: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.max.time())
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, min(1.0, number))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
