from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import settings


def save_market_data(
    symbol: str,
    data: Any,
    *,
    source: str,
    trade_date: date,
    root: Path | None = None,
    raw_data: Any | None = None,
    rejected_data: pd.DataFrame | None = None,
    quality_report: dict[str, Any] | None = None,
    retrieved_at: datetime | None = None,
    data_version: str | None = None,
    frequency: str = "1d",
    price_adjustment: str = "none",
) -> dict[str, str]:
    """保存原始行情、有效标准化行情、异常行和质量元数据。"""
    root = root or settings.data_dir
    stamp = _as_utc(retrieved_at or datetime.now(timezone.utc))
    safe_symbol = _safe_symbol(symbol)
    date_text = trade_date.isoformat()
    processed_dir = root / "processed" / "market"
    processed_dir.mkdir(parents=True, exist_ok=True)
    base = processed_dir / f"{safe_symbol}_{date_text}"

    paths: dict[str, str] = {}
    if raw_data is not None:
        raw_dir = root / "raw" / "market" / _safe_component(source) / date_text
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_base = raw_dir / f"{safe_symbol}_raw.json"
        raw_path = _non_overwriting_path(raw_base, stamp)
        raw_path.write_text(json.dumps(_jsonable(raw_data), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["raw_path"] = str(raw_path)

    data_path: Path
    if isinstance(data, pd.DataFrame) or hasattr(data, "to_parquet"):
        try:
            data_path = base.with_suffix(".parquet")
            data.to_parquet(data_path, index=False)
        except Exception:  # noqa: BLE001
            data_path = base.with_suffix(".csv")
            data.to_csv(data_path, index=False, encoding="utf-8-sig")
    elif isinstance(data, (dict, list)):
        data_path = base.with_suffix(".json")
        data_path.write_text(json.dumps(_jsonable(data), ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    else:
        data_path = base.with_suffix(".txt")
        data_path.write_text(str(data), encoding="utf-8")
    paths["data_path"] = str(data_path)

    if rejected_data is not None and not rejected_data.empty:
        rejected_dir = processed_dir / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        rejected_path = rejected_dir / f"{safe_symbol}_{date_text}_rejected.jsonl"
        with rejected_path.open("w", encoding="utf-8") as handle:
            for record in rejected_data.to_dict(orient="records"):
                handle.write(json.dumps(_jsonable(record), ensure_ascii=False, default=str) + "\n")
        paths["rejected_path"] = str(rejected_path)

    quality_path = base.with_name(base.name + "_quality.json")
    report = quality_report or {
        "status": "not_checked",
        "input_rows": _row_count(data),
        "output_rows": _row_count(data),
        "rejected_rows": 0,
        "duplicates_removed": 0,
        "warnings": [],
        "errors": [],
        "column_mapping": {},
    }
    quality_path.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["quality_path"] = str(quality_path)

    metadata_path = base.with_name(base.name + "_metadata.json")
    metadata = {
        "symbol": symbol,
        "source": source,
        "trade_date": date_text,
        "retrieved_at": stamp.isoformat(),
        "frequency": frequency,
        "price_adjustment": price_adjustment,
        "data_version": data_version,
        "quality_report": report,
        **paths,
    }
    metadata_path.write_text(json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["metadata_path"] = str(metadata_path)
    return paths


def save_technical_indicators(
    symbol: str,
    data: pd.DataFrame,
    *,
    source: str,
    trade_date: date,
    indicator_summary: dict[str, Any] | None = None,
    quality_gate: dict[str, Any] | None = None,
    root: Path | None = None,
    retrieved_at: datetime | None = None,
    data_version: str | None = None,
    frequency: str = "1d",
    price_adjustment: str = "none",
) -> dict[str, str]:
    """保存技术指标结果及其计算元数据，不覆盖同一日期的既有结果。"""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("技术指标结果必须是 pandas DataFrame")

    root = root or settings.data_dir
    stamp = _as_utc(retrieved_at or datetime.now(timezone.utc))
    safe_symbol = _safe_symbol(symbol)
    date_text = trade_date.isoformat()
    processed_dir = root / "processed" / "market" / "indicators"
    processed_dir.mkdir(parents=True, exist_ok=True)
    base = processed_dir / f"{safe_symbol}_{date_text}_technical_indicators"
    data_base = _non_overwriting_path(base.with_name(base.name + ".parquet"), stamp)

    try:
        data.to_parquet(data_base, index=False)
    except Exception:  # noqa: BLE001
        data_base = data_base.with_name(data_base.stem + ".csv")
        data.to_csv(data_base, index=False, encoding="utf-8-sig")

    paths = {"data_path": str(data_base)}
    metadata_base = data_base.with_name(data_base.stem + "_metadata.json")
    metadata_path = _non_overwriting_path(metadata_base, stamp)
    metadata = {
        "symbol": symbol,
        "source": source,
        "trade_date": date_text,
        "retrieved_at": stamp.isoformat(),
        "frequency": frequency,
        "price_adjustment": price_adjustment,
        "data_version": data_version,
        "row_count": len(data),
        "columns": [str(column) for column in data.columns],
        "quality_gate": quality_gate or {},
        "indicator_summary": indicator_summary or {},
        "data_path": str(data_base),
    }
    metadata_path.write_text(
        json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["metadata_path"] = str(metadata_path)
    return paths



def save_technical_signals(
    symbol: str,
    data: pd.DataFrame,
    *,
    source: str,
    trade_date: date,
    signal_summary: dict[str, Any] | None = None,
    quality_gate: dict[str, Any] | None = None,
    root: Path | None = None,
    retrieved_at: datetime | None = None,
    data_version: str | None = None,
    frequency: str = "1d",
    price_adjustment: str = "none",
) -> dict[str, str]:
    """保存逐日技术信号和最新信号摘要，不覆盖同一日期的既有结果。"""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("技术信号结果必须是 pandas DataFrame")

    root = root or settings.data_dir
    stamp = _as_utc(retrieved_at or datetime.now(timezone.utc))
    safe_symbol = _safe_symbol(symbol)
    date_text = trade_date.isoformat()
    processed_dir = root / "processed" / "market" / "signals"
    processed_dir.mkdir(parents=True, exist_ok=True)
    base = processed_dir / f"{safe_symbol}_{date_text}_technical_signals"
    data_base = _non_overwriting_path(base.with_name(base.name + ".parquet"), stamp)

    try:
        data.to_parquet(data_base, index=False)
    except Exception:  # noqa: BLE001
        data_base = data_base.with_name(data_base.stem + ".csv")
        data.to_csv(data_base, index=False, encoding="utf-8-sig")

    paths = {"data_path": str(data_base)}
    metadata_base = data_base.with_name(data_base.stem + "_metadata.json")
    metadata_path = _non_overwriting_path(metadata_base, stamp)
    metadata = {
        "symbol": symbol,
        "source": source,
        "trade_date": date_text,
        "retrieved_at": stamp.isoformat(),
        "frequency": frequency,
        "price_adjustment": price_adjustment,
        "data_version": data_version,
        "row_count": len(data),
        "columns": [str(column) for column in data.columns],
        "quality_gate": quality_gate or {},
        "signal_summary": signal_summary or {},
        "data_path": str(data_base),
    }
    metadata_path.write_text(
        json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["metadata_path"] = str(metadata_path)
    return paths


def save_next_day_prediction(
    symbol: str,
    prediction: Any,
    *,
    trade_date: date,
    source: str,
    root: Path | None = None,
    retrieved_at: datetime | None = None,
    data_version: str | None = None,
) -> dict[str, str]:
    """保存次日预测 JSON 和审计元数据，不覆盖同一日期的既有结果。"""
    root = root or settings.data_dir
    stamp = _as_utc(retrieved_at or datetime.now(timezone.utc))
    safe_symbol = _safe_symbol(symbol)
    date_text = trade_date.isoformat()
    processed_dir = root / "processed" / "market" / "predictions"
    processed_dir.mkdir(parents=True, exist_ok=True)
    base = processed_dir / f"{safe_symbol}_{date_text}_next_day_prediction.json"
    prediction_path = _non_overwriting_path(base, stamp)
    payload = prediction.to_dict() if hasattr(prediction, "to_dict") else _jsonable(prediction)
    record = {
        "symbol": symbol,
        "source": source,
        "trade_date": date_text,
        "retrieved_at": stamp.isoformat(),
        "data_version": data_version,
        "prediction": payload,
    }
    prediction_path.write_text(
        json.dumps(_jsonable(record), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {"prediction_path": str(prediction_path)}

def save_secondary_market_audit(
    symbol: str,
    source: str,
    trade_date: date,
    raw_data: Any | None = None,
    comparison_report: dict[str, Any] | None = None,
    *,
    quality_report: dict[str, Any] | None = None,
    retrieved_at: datetime | None = None,
    data_version: str | None = None,
    error: str | None = None,
    root: Path | None = None,
) -> dict[str, str]:
    """保存备用行情源原始响应和主备交叉验证明细，形成可追溯审计链路。"""
    root = root or settings.data_dir
    stamp = _as_utc(retrieved_at or datetime.now(timezone.utc))
    safe_symbol = _safe_symbol(symbol)
    safe_source = _safe_component(source)
    date_text = trade_date.isoformat()
    paths: dict[str, str] = {}

    if raw_data is not None:
        raw_dir = root / "raw" / "market" / safe_source / date_text
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_base = raw_dir / f"{safe_symbol}_secondary_raw.json"
        raw_path = _non_overwriting_path(raw_base, stamp)
        raw_payload = {
            "symbol": symbol,
            "source": source,
            "trade_date": date_text,
            "retrieved_at": stamp.isoformat(),
            "data_version": data_version,
            "raw_data": raw_data,
        }
        raw_path.write_text(
            json.dumps(_jsonable(raw_payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["raw_path"] = str(raw_path)

    audit_dir = root / "processed" / "market" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_base = audit_dir / f"{safe_symbol}_{date_text}_{safe_source}_cross_validation.json"
    audit_path = _non_overwriting_path(audit_base, stamp)
    audit_payload: dict[str, Any] = {
        "symbol": symbol,
        "source": source,
        "trade_date": date_text,
        "retrieved_at": stamp.isoformat(),
        "data_version": data_version,
        "status": (comparison_report or {}).get("status", "unavailable"),
        "comparison": comparison_report or {},
        "quality_report": quality_report,
    }
    if paths.get("raw_path"):
        audit_payload["raw_path"] = paths["raw_path"]
    if error:
        audit_payload["error"] = error
    audit_path.write_text(
        json.dumps(_jsonable(audit_payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths["audit_path"] = str(audit_path)
    return paths


def _safe_symbol(value: str) -> str:
    return _safe_component(value.replace("/", "_").replace("\\", "_"))


def _safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value)) or "unknown"


def _non_overwriting_path(base: Path, stamp: datetime) -> Path:
    if not base.exists():
        return base
    suffix = stamp.strftime('%Y%m%dT%H%M%S%fZ')
    candidate = base.with_name(f"{base.stem}_{suffix}{base.suffix}")
    counter = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}_{suffix}_{counter}{base.suffix}")
        counter += 1
    return candidate


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _row_count(value: Any) -> int:
    try:
        return len(value)
    except TypeError:
        return 1


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if not isinstance(value, (str, bytes, bool, int, float)):
        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass
    return value
