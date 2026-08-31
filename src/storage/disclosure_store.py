from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import settings


class DisclosureStore:
    """保存公告/财报原始响应、标准化结果、异常行和质量报告。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.data_dir

    def save_batch(
        self,
        symbol: str,
        source: str,
        raw_data: Any,
        data: pd.DataFrame,
        *,
        rejected_data: pd.DataFrame | None = None,
        quality_report: dict[str, Any] | None = None,
        retrieved_at: datetime | None = None,
        data_version: str | None = None,
    ) -> dict[str, str]:
        stamp = retrieved_at or datetime.now(timezone.utc)
        date_text = stamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
        safe_symbol = _safe(symbol)
        safe_source = _safe(source)
        paths: dict[str, str] = {}
        raw_dir = self.root / "raw" / "disclosures" / safe_source / date_text
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{safe_symbol}_{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        raw_path.write_text(json.dumps(_jsonable({"symbol": symbol, "source": source, "retrieved_at": stamp.isoformat(), "data_version": data_version, "raw_data": raw_data}), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["raw_path"] = str(raw_path)

        processed_dir = self.root / "processed" / "disclosures"
        processed_dir.mkdir(parents=True, exist_ok=True)
        processed_path = processed_dir / f"{safe_symbol}_{date_text}.jsonl"
        with processed_path.open("a", encoding="utf-8") as handle:
            for record in data.to_dict(orient="records") if isinstance(data, pd.DataFrame) else []:
                handle.write(json.dumps(_jsonable(record), ensure_ascii=False, default=str) + "\n")
        paths["processed_path"] = str(processed_path)

        if isinstance(rejected_data, pd.DataFrame) and not rejected_data.empty:
            rejected_path = processed_dir / f"{safe_symbol}_{date_text}_rejected.jsonl"
            with rejected_path.open("a", encoding="utf-8") as handle:
                for record in rejected_data.to_dict(orient="records"):
                    handle.write(json.dumps(_jsonable(record), ensure_ascii=False, default=str) + "\n")
            paths["rejected_path"] = str(rejected_path)

        quality_path = processed_dir / f"{safe_symbol}_{date_text}_quality.json"
        quality_path.write_text(json.dumps(_jsonable(quality_report or {}), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["quality_path"] = str(quality_path)
        metadata = {"symbol": symbol, "source": source, "retrieved_at": stamp.isoformat(), "data_version": data_version, "rows": len(data), **paths}
        metadata_path = processed_dir / f"{safe_symbol}_{date_text}_metadata.json"
        metadata_path.write_text(json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["metadata_path"] = str(metadata_path)
        return paths


def _safe(value: Any) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(value)) or "unknown"


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.DataFrame): return value.to_dict(orient="records")
    if isinstance(value, pd.Series): return value.to_dict()
    if isinstance(value, dict): return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime)): return value.isoformat()
    if value is pd.NA: return None
    if value is None or isinstance(value, (str, bool, int, float)): return value
    try:
        if bool(pd.isna(value)): return None
    except (TypeError, ValueError): pass
    return str(value)
