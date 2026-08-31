"""预测输入数据质量门禁。

门禁位于行情采集/质量检查之后、技术指标和预测逻辑之前，用于防止
空数据、严重质量错误或主备源不一致的数据直接进入预测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping

import pandas as pd

from src.market.schema import REQUIRED_COLUMNS


GATE_RULES_VERSION = "1.0"
GateStatus = Literal["approved", "degraded", "blocked"]


@dataclass(frozen=True)
class QualityGateResult:
    """预测输入门禁的可序列化结果。"""

    status: GateStatus
    can_predict: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: dict[str, Any]
    data_cutoff: str | None
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        """转换成可写入质量报告和 Markdown 报告的字典。"""
        return {
            "gate_rules_version": GATE_RULES_VERSION,
            "status": self.status,
            "can_predict": self.can_predict,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "checks": self.checks,
            "data_cutoff": self.data_cutoff,
            "checked_at": self.checked_at,
        }


class PredictionInputBlockedError(RuntimeError):
    """预测输入未通过门禁。"""

    def __init__(self, result: QualityGateResult) -> None:
        self.result = result
        detail = "; ".join(result.reasons) or "未通过预测输入质量门禁"
        super().__init__(detail)


def evaluate_prediction_input(
    data: pd.DataFrame | Any,
    quality_report: Mapping[str, Any] | None = None,
    *,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    retrieved_at: datetime | None = None,
    route_degraded: bool = False,
) -> QualityGateResult:
    """评估行情是否可以作为预测输入。

    ``approved`` 表示质量检查和主备核验均正常；``degraded`` 表示主源可用，
    但存在备用源不可用、交叉验证跳过、路由降级或普通质量警告；``blocked``
    表示数据不安全，不应进入任何预测计算。
    """
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    report = dict(quality_report or {})
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    checks["has_data"] = not frame.empty
    checks["required_columns"] = [column for column in REQUIRED_COLUMNS if column in frame.columns]
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    checks["missing_columns"] = missing_columns
    if frame.empty:
        reasons.append("预测输入行情为空")
    if missing_columns:
        reasons.append(f"预测输入缺少必填列: {', '.join(missing_columns)}")

    if not frame.empty and "symbol" in frame.columns:
        symbols = sorted({str(value) for value in frame["symbol"].dropna()})
        checks["symbols"] = symbols
        if symbol and any(value != symbol for value in symbols):
            reasons.append(f"行情标的与请求不一致: 请求 {symbol}，实际 {', '.join(symbols)}")
        if not symbols:
            reasons.append("预测输入缺少有效标的代码")
    else:
        checks["symbols"] = []

    invalid_trade_dates = _invalid_trade_date_count(frame)
    checks["invalid_trade_date_rows"] = invalid_trade_dates
    if invalid_trade_dates:
        reasons.append(f"预测输入包含 {invalid_trade_dates} 条无效交易日期")
    data_start, data_cutoff = _trade_date_range(frame)
    checks["data_range"] = {"start_date": data_start, "end_date": data_cutoff}
    checks["data_cutoff"] = data_cutoff
    checks["requested_range"] = {
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
    }
    if data_start and start_date and data_start < start_date.isoformat():
        reasons.append(f"行情数据早于请求起始日期: {data_start} < {start_date.isoformat()}")
    if data_cutoff and end_date and data_cutoff > end_date.isoformat():
        reasons.append(f"行情数据晚于请求结束日期: {data_cutoff} > {end_date.isoformat()}")

    quality_status = str(report.get("status", "missing"))
    checks["quality_status"] = quality_status
    quality_errors = _as_text_list(report.get("errors"))
    checks["quality_errors"] = quality_errors
    if quality_status == "missing":
        reasons.append("缺少主源质量报告")
    elif quality_status == "rejected":
        reasons.append("主源质量检查状态为 rejected")
    elif quality_status not in {"validated", "validated_with_warning"}:
        reasons.append(f"主源质量报告状态未通过: {quality_status}")
    if quality_errors:
        reasons.extend(f"主源质量错误: {error}" for error in quality_errors)

    rejected_rows = _as_non_negative_int(report.get("rejected_rows"))
    checks["rejected_rows"] = rejected_rows
    if rejected_rows:
        warnings.append(f"主源质量检查拒绝 {rejected_rows} 条记录，预测可信度降低")

    quality_warnings = _as_text_list(report.get("warnings"))
    if quality_warnings:
        warnings.extend(f"主源质量警告: {warning}" for warning in quality_warnings)

    calendar_report = report.get("calendar_validation")
    calendar_status = "missing"
    unexpected_dates: list[str] = []
    if isinstance(calendar_report, Mapping):
        calendar_status = str(calendar_report.get("status", "unknown"))
        unexpected_dates = _as_text_list(calendar_report.get("unexpected_non_trading_dates"))
    checks["calendar_validation"] = {
        "status": calendar_status,
        "unexpected_non_trading_dates": unexpected_dates,
    }
    if unexpected_dates:
        reasons.append(f"交易日历发现非交易日行情: {', '.join(unexpected_dates)}")
    elif calendar_status == "rejected":
        reasons.append("交易日历校验状态为 rejected")
    elif calendar_status in {"unavailable", "skipped", "missing", "unknown"}:
        warnings.append(f"交易日历校验状态为 {calendar_status}，未能完全核验交易日")

    cross_report = report.get("cross_validation")
    cross_status = "missing"
    if isinstance(cross_report, Mapping):
        cross_status = str(cross_report.get("status", "unknown"))
    checks["cross_validation"] = {"status": cross_status}
    if cross_status == "mismatch":
        reasons.append("主备行情交叉验证为 mismatch，需人工核验后才能预测")
    elif cross_status in {"unavailable", "skipped", "missing", "unknown"}:
        warnings.append(f"主备行情交叉验证状态为 {cross_status}")

    if route_degraded:
        warnings.append("行情路由使用了备用主源，属于降级数据")

    if not reasons:
        degraded_conditions = (
            route_degraded,
            bool(rejected_rows),
            quality_status == "validated_with_warning",
            bool(quality_warnings),
            calendar_status in {"unavailable", "skipped", "missing", "unknown"},
            cross_status in {"unavailable", "skipped", "missing", "unknown"},
        )
        status: GateStatus = "degraded" if any(degraded_conditions) else "approved"
    else:
        status = "blocked"

    checked = _as_utc(retrieved_at or datetime.now(timezone.utc))
    return QualityGateResult(
        status=status,
        can_predict=status != "blocked",
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
        checks=checks,
        data_cutoff=data_cutoff,
        checked_at=checked.isoformat(),
    )


def require_prediction_input(
    data: pd.DataFrame | Any,
    quality_report: Mapping[str, Any] | None = None,
    *,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    retrieved_at: datetime | None = None,
    route_degraded: bool = False,
) -> QualityGateResult:
    """执行门禁并在 blocked 时抛出异常，供预测入口调用。"""
    result = evaluate_prediction_input(
        data,
        quality_report,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        retrieved_at=retrieved_at,
        route_degraded=route_degraded,
    )
    if result.status == "blocked":
        raise PredictionInputBlockedError(result)
    return result


def _invalid_trade_date_count(frame: pd.DataFrame) -> int:
    if frame.empty or "trade_date" not in frame.columns:
        return 0
    return int(pd.to_datetime(frame["trade_date"], errors="coerce").isna().sum())


def _trade_date_range(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if frame.empty or "trade_date" not in frame.columns:
        return None, None
    parsed = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
    if parsed.empty:
        return None, None
    return parsed.min().date().isoformat(), parsed.max().date().isoformat()


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
