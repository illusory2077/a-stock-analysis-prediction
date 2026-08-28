from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings


def save_market_data(symbol: str, data: Any, *, source: str, trade_date: date, root: Path | None = None) -> dict[str, str]:
    """保存行情结果；优先 Parquet，失败时回退 CSV，并保存元数据。"""
    root = root or settings.data_dir
    target_dir = root / "processed" / "market"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")
    base = target_dir / f"{safe_symbol}_{trade_date.isoformat()}"
    data_path: Path

    if hasattr(data, "to_parquet"):
        try:
            data_path = base.with_name(base.name + ".parquet")
            data.to_parquet(data_path, index=False)
        except Exception:  # noqa: BLE001
            data_path = base.with_name(base.name + ".csv")
            data.to_csv(data_path, index=False, encoding="utf-8-sig")
    elif isinstance(data, (dict, list)):
        data_path = base.with_name(base.name + ".json")
        data_path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    else:
        data_path = base.with_name(base.name + ".txt")
        data_path.write_text(str(data), encoding="utf-8")

    metadata_path = base.with_name(base.name + "_metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "source": source,
                "trade_date": trade_date.isoformat(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "data_path": str(data_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"data_path": str(data_path), "metadata_path": str(metadata_path)}

