"""主备行情标准化结果的交叉验证。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


DEFAULT_CLOSE_THRESHOLD = 0.005
DEFAULT_VOLUME_THRESHOLD = 0.02
DEFAULT_AMOUNT_THRESHOLD = 0.02


def compare_daily_bars(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    *,
    primary_source: str,
    secondary_source: str,
    close_threshold: float = DEFAULT_CLOSE_THRESHOLD,
    volume_threshold: float = DEFAULT_VOLUME_THRESHOLD,
    amount_threshold: float = DEFAULT_AMOUNT_THRESHOLD,
) -> dict[str, Any]:
    """比较两份已标准化日线，差异超过阈值只标记待核验，不覆盖主源结果。"""
    key_columns = ["symbol", "trade_date"]
    if any(column not in primary.columns for column in key_columns) or any(column not in secondary.columns for column in key_columns):
        return _unavailable(primary_source, secondary_source, "缺少 symbol/trade_date")
    if primary.empty or secondary.empty:
        return _unavailable(primary_source, secondary_source, "任一数据源没有有效记录")

    left = primary.copy().drop_duplicates(key_columns)
    right = secondary.copy().drop_duplicates(key_columns)
    # pandas 会在 merge/concat 时比较 attrs；标准化结果的 attrs 可能包含 DataFrame，
    # 清空元数据可避免 DataFrame truth-value 异常，比较只依赖标准字段。
    left.attrs = {}
    right.attrs = {}
    merged = left.merge(right, on=key_columns, how="outer", suffixes=("_primary", "_secondary"), indicator=True)
    both = merged.loc[merged["_merge"] == "both"].copy()
    missing_primary = merged.loc[merged["_merge"] == "right_only"]
    missing_secondary = merged.loc[merged["_merge"] == "left_only"]
    warnings: list[str] = []
    metrics: dict[str, dict[str, float | None]] = {}
    mismatches: list[str] = []

    if both.empty:
        return {
            "status": "unavailable",
            "primary_source": primary_source,
            "secondary_source": secondary_source,
            "compared_rows": 0,
            "missing_primary_rows": len(missing_primary),
            "missing_secondary_rows": len(missing_secondary),
            "metrics": {},
            "warnings": ["两个数据源没有可比的交易日记录"],
        }
    if not missing_primary.empty:
        warning = f"备用源多出 {len(missing_primary)} 条主源没有的记录"
        warnings.append(warning)
        mismatches.append(warning)
    if not missing_secondary.empty:
        warning = f"备用源缺少主源的 {len(missing_secondary)} 条记录"
        warnings.append(warning)
        mismatches.append(warning)

    for field, threshold in (("close", close_threshold), ("volume", volume_threshold), ("amount", amount_threshold)):
        primary_field = f"{field}_primary"
        secondary_field = f"{field}_secondary"
        if primary_field not in both.columns or secondary_field not in both.columns:
            warnings.append(f"无法比较 {field}：字段缺失")
            continue
        differences = _relative_differences(both[primary_field], both[secondary_field])
        if differences.empty:
            warnings.append(f"无法比较 {field}：双方均无有效值")
            metrics[field] = {"max_diff_pct": None, "mean_diff_pct": None, "threshold_pct": threshold * 100}
            continue
        max_diff = float(differences.max())
        mean_diff = float(differences.mean())
        metrics[field] = {
            "max_diff_pct": max_diff * 100,
            "mean_diff_pct": mean_diff * 100,
            "threshold_pct": threshold * 100,
        }
        if max_diff > threshold:
            mismatches.append(f"{field} 最大差异 {max_diff:.4%} 超过阈值 {threshold:.4%}")

    if mismatches:
        warnings.extend(mismatches)
    status = "mismatch" if mismatches else "matched"
    return {
        "status": status,
        "primary_source": primary_source,
        "secondary_source": secondary_source,
        "compared_rows": len(both),
        "missing_primary_rows": len(missing_primary),
        "missing_secondary_rows": len(missing_secondary),
        "metrics": metrics,
        "warnings": warnings,
    }


def _relative_differences(left: pd.Series, right: pd.Series) -> pd.Series:
    left_numeric = pd.to_numeric(left, errors="coerce")
    right_numeric = pd.to_numeric(right, errors="coerce")
    denominator = pd.concat([left_numeric.abs(), right_numeric.abs()], axis=1).max(axis=1)
    differences = (left_numeric - right_numeric).abs().div(denominator.where(denominator > 0))
    differences = differences.mask((denominator == 0) & left_numeric.notna() & right_numeric.notna(), 0.0)
    return differences.dropna()


def _unavailable(primary_source: str, secondary_source: str, warning: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "primary_source": primary_source,
        "secondary_source": secondary_source,
        "compared_rows": 0,
        "missing_primary_rows": 0,
        "missing_secondary_rows": 0,
        "metrics": {},
        "warnings": [warning],
    }
