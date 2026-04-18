import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict[str, Any]:
    return {
        "last_run": None,
        "batch_state": {
            "active": False,
            "started_at": None,
            "completed_ids": [],
            "last_processed_date": None,
            "provider": None,
            "account": None,
        },
        "history": [],
    }


class Metadata:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = _empty()
        self.load()

    def load(self) -> None:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            base = _empty()
            base.update(loaded)
            base["batch_state"] = {**_empty()["batch_state"], **loaded.get("batch_state", {})}
            self.data = base
        else:
            self.data = _empty()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        tmp.replace(self.path)

    @property
    def last_run_at(self) -> datetime | None:
        last = self.data.get("last_run")
        if not last:
            return None
        ts = last.get("timestamp")
        return datetime.fromisoformat(ts) if ts else None

    def record_run(
        self,
        provider: str,
        account: str,
        emails_processed: int,
        date_from: datetime | None,
        date_to: datetime | None,
        mode: str,
    ) -> None:
        entry = {
            "timestamp": _now_iso(),
            "provider": provider,
            "account": account,
            "emails_processed": emails_processed,
            "mode": mode,
            "date_range": {
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None,
            },
        }
        self.data["last_run"] = entry
        self.data["history"].append(entry)
        self.data["history"] = self.data["history"][-50:]
        self.save()

    # --- batch tracking ---

    @property
    def batch_completed_ids(self) -> set[str]:
        return set(self.data["batch_state"].get("completed_ids", []))

    def begin_batch(self, provider: str, account: str) -> None:
        state = self.data["batch_state"]
        if not state.get("active"):
            state.update(
                active=True,
                started_at=_now_iso(),
                completed_ids=[],
                provider=provider,
                account=account,
            )
            self.save()

    def mark_batch_processed(self, email_ids: list[str], last_date: datetime | None) -> None:
        state = self.data["batch_state"]
        ids = state.get("completed_ids", [])
        ids.extend(email_ids)
        state["completed_ids"] = ids
        if last_date:
            state["last_processed_date"] = last_date.isoformat()
        self.save()

    def end_batch(self) -> None:
        self.data["batch_state"]["active"] = False
        self.save()

    def reset_batch(self) -> None:
        self.data["batch_state"] = _empty()["batch_state"]
        self.save()
