"""行情质量检查和重复记录处理。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .schema import DUPLICATE_KEY_COLUMNS, QUALITY_RULES_VERSION, REQUIRED_COLUMNS


@dataclass
class QualityResult:
    data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any]


def validate_daily_bars(
    data: pd.DataFrame,
    *,
    rejected_data: pd.DataFrame | None = None,
    input_rows: int | None = None,
    warnings: list[str] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> QualityResult:
    """检查标准行情，并返回有效行、异常行和可序列化质量报告。"""
    frame = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    frame = frame.reset_index(drop=True)
    # 避免 pandas 在 concat/astype 时比较包含 DataFrame 的 attrs。
    frame.attrs = {}
    rejected_parts: list[pd.DataFrame] = []
    if rejected_data is not None and not rejected_data.empty:
        rejected_parts.append(rejected_data.copy())
        rejected_parts[-1].attrs = {}
    errors: list[str] = []
    report_warnings = list(warnings or [])
    duplicates_removed = 0

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        report = _report(
            status="rejected",
            input_rows=input_rows if input_rows is not None else len(frame),
            output_rows=0,
            rejected_rows=input_rows if input_rows is not None else len(frame),
            duplicates_removed=0,
            warnings=report_warnings,
            errors=[f"缺少必填列: {', '.join(missing_columns)}"],
            column_mapping=column_mapping,
        )
        return QualityResult(pd.DataFrame(columns=frame.columns), frame, report)

    row_errors: dict[int, list[str]] = {}
    for index, row in frame.iterrows():
        reasons: list[str] = []
        for column in REQUIRED_COLUMNS:
            if _missing(row.get(column)):
                reasons.append(f"{column} 为空")
        for field in ("open", "high", "low", "close"):
            if not _is_number(row.get(field)):
                reasons.append(f"{field} 不是有效数值")
            elif float(row[field]) < 0:
                reasons.append(f"{field} 不能为负数")
        for field in ("volume", "amount"):
            if not _missing(row.get(field)):
                if not _is_number(row.get(field)):
                    reasons.append(f"{field} 不是有效数值")
                elif float(row[field]) < 0:
                    reasons.append(f"{field} 不能为负数")
        if all(_is_number(row.get(field)) for field in ("open", "high", "low", "close")):
            open_, high, low, close = (float(row[field]) for field in ("open", "high", "low", "close"))
            if high < max(open_, close):
                reasons.append("high 小于 open/close 的最大值")
            if low > min(open_, close):
                reasons.append("low 大于 open/close 的最小值")
        if reasons:
            row_errors[index] = reasons

    if row_errors:
        bad = frame.loc[list(row_errors)].copy()
        bad["_rejection_reason"] = ["; ".join(row_errors[index]) for index in bad.index]
        rejected_parts.append(bad)
        frame = frame.drop(index=list(row_errors)).reset_index(drop=True)

    # 日期顺序只告警，不改变交易日集合；随后按统一主键排序。
    if not frame.empty and "trade_date" in frame.columns:
        dates = pd.to_datetime(frame["trade_date"], errors="coerce")
        if not dates.is_monotonic_increasing:
            report_warnings.append("输入行情日期未升序排列，已自动排序")

    # 完全重复只保留一条；同一主键但内容冲突的记录全部拒绝。
    if not frame.empty:
        key_columns = list(DUPLICATE_KEY_COLUMNS)
        duplicate_groups = frame.groupby(key_columns, dropna=False, sort=False)
        conflicting_indices: list[int] = []
        exact_duplicate_indices: list[int] = []
        compare_columns = [column for column in frame.columns if not column.startswith("extra_")]
        for _, group in duplicate_groups:
            if len(group) <= 1:
                continue
            comparable = group[compare_columns].astype(str).drop_duplicates()
            if len(comparable) == 1:
                exact_duplicate_indices.extend(group.index[1:].tolist())
            else:
                conflicting_indices.extend(group.index.tolist())
        if exact_duplicate_indices:
            duplicates_removed = len(exact_duplicate_indices)
            frame = frame.drop(index=exact_duplicate_indices).reset_index(drop=True)
            report_warnings.append(f"删除完全重复记录 {duplicates_removed} 条")
        if conflicting_indices:
            conflicting = frame.loc[conflicting_indices].copy()
            conflicting["_rejection_reason"] = "同一主键存在冲突行情记录"
            rejected_parts.append(conflicting)
            frame = frame.drop(index=conflicting_indices).reset_index(drop=True)
            errors.append(f"发现同一主键的冲突行情记录 {len(conflicting_indices)} 条，已拒绝")

    if not frame.empty:
        frame = frame.sort_values(["symbol", "trade_date"], kind="stable").reset_index(drop=True)

    rejected = _concat_rejected(rejected_parts, columns=[*data.columns, "_rejection_reason"])
    total_input = input_rows if input_rows is not None else len(data) + (len(rejected_data) if rejected_data is not None else 0)
    status = "validated"
    if not frame.empty and (report_warnings or not rejected.empty):
        status = "validated_with_warning"
    if frame.empty:
        status = "rejected"
    report = _report(
        status=status,
        input_rows=total_input,
        output_rows=len(frame),
        rejected_rows=len(rejected),
        duplicates_removed=duplicates_removed,
        warnings=list(dict.fromkeys(report_warnings)),
        errors=errors,
        column_mapping=column_mapping,
    )
    return QualityResult(frame, rejected, report)


def _report(**kwargs: Any) -> dict[str, Any]:
    return {"quality_rules_version": QUALITY_RULES_VERSION, **kwargs}


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _is_number(value: Any) -> bool:
    if _missing(value) or isinstance(value, bool):
        return False
    try:
        return bool(pd.notna(value)) and float(value) == float(value)
    except (TypeError, ValueError):
        return False


def _concat_rejected(parts: list[pd.DataFrame], *, columns: list[str]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(columns=columns)
    result = pd.concat(parts, ignore_index=True, sort=False)
    for column in columns:
        if column not in result.columns:
            result[column] = None
    return result



