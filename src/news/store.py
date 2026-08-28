from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config.settings import settings


class NewsStore:
    """保存新闻原始响应和标准化 JSONL 数据。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.data_dir

    def save_raw(self, provider: str, response: dict[str, Any], *, retrieved_at: datetime | None = None) -> Path:
        stamp = retrieved_at or datetime.now(timezone.utc)
        target_dir = self.root / "raw" / "news" / provider / stamp.strftime("%Y-%m-%d")
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        path.write_text(json.dumps(response, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def append_processed(self, items: Iterable[dict[str, Any]], *, processed_at: datetime | None = None) -> Path:
        stamp = processed_at or datetime.now(timezone.utc)
        target_dir = self.root / "processed" / "news"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"news_{stamp.strftime('%Y%m%d')}.jsonl"

        existing_ids: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("news_id"):
                    existing_ids.add(str(record["news_id"]))

        with path.open("a", encoding="utf-8") as handle:
            for item in items:
                news_id = str(item.get("news_id") or "")
                if news_id and news_id in existing_ids:
                    continue
                handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                if news_id:
                    existing_ids.add(news_id)
        return path

    def save_batch(
        self,
        provider: str,
        response: dict[str, Any],
        items: Iterable[dict[str, Any]],
        *,
        retrieved_at: datetime | None = None,
    ) -> dict[str, str]:
        raw_path = self.save_raw(provider, response, retrieved_at=retrieved_at)
        processed_path = self.append_processed(items, processed_at=retrieved_at)
        return {"raw_path": str(raw_path), "processed_path": str(processed_path)}
