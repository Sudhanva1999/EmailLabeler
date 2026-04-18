"""Append-only JSONL log of emails the classifier gave up on after retries."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DroppedEmailLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, email: Any, error: str, attempts: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        date = getattr(email, "date", None)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "id": getattr(email, "id", None),
            "subject": getattr(email, "subject", "") or "",
            "sender": getattr(email, "sender", "") or "",
            "date": date.isoformat() if date else None,
            "attempts": attempts,
            "error": error,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)


def default_dropped_log() -> DroppedEmailLog:
    path = Path(os.getenv("DROPPED_LOG_FILE", "dropped_emails.jsonl"))
    return DroppedEmailLog(path)
