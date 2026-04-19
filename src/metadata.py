"""Facade over src.cache / src.db that preserves the legacy public surface
used by batch_processor.py, main.py, and the TUI:

    - Metadata()
    - .data (dict with last_run, batch_state, history — rebuilt from the DB)
    - .last_run_at
    - .record_run(...)
    - .batch_completed_ids
    - .begin_batch(...), .mark_batch_processed(...), .end_batch(), .reset_batch()

All state lives in SQLite; there is no JSON file on disk any more.
"""

from datetime import datetime
from typing import Any

from .cache import Cache
from .db import Database


class Metadata:
    def __init__(self, cache: Cache | None = None) -> None:
        self._cache = cache or Cache(Database())
        self._batch_provider: str | None = None
        self._batch_account: str | None = None
        self.data: dict[str, Any] = self._snapshot_dict()
        self._batch_provider = self.data["batch_state"].get("provider")
        self._batch_account = self.data["batch_state"].get("account")

    def _snapshot_dict(self) -> dict[str, Any]:
        last = self._cache.last_run()
        history = [r.to_dict() for r in self._cache.recent_runs(50)]
        return {
            "last_run": last.to_dict() if last else None,
            "batch_state": self._cache.batch_state_dict(),
            "history": history,
        }

    def _refresh(self) -> None:
        self.data = self._snapshot_dict()
        self._batch_provider = self.data["batch_state"].get("provider")
        self._batch_account = self.data["batch_state"].get("account")

    @property
    def cache(self) -> Cache:
        return self._cache

    # --- last run ---

    @property
    def last_run_at(self) -> datetime | None:
        last = self._cache.last_run()
        if not last or not last.timestamp:
            return None
        try:
            return datetime.fromisoformat(last.timestamp)
        except ValueError:
            return None

    def record_run(
        self,
        provider: str,
        account: str,
        emails_processed: int,
        date_from: datetime | None,
        date_to: datetime | None,
        mode: str,
    ) -> None:
        self._cache.record_run(
            provider=provider,
            account=account,
            emails_processed=emails_processed,
            date_from=date_from,
            date_to=date_to,
            mode=mode,
        )
        self._refresh()

    # --- batch tracking ---

    @property
    def batch_completed_ids(self) -> set[str]:
        provider = self._batch_provider or self.data["batch_state"].get("provider")
        account = self._batch_account or self.data["batch_state"].get("account")
        if not provider or not account:
            return set()
        return self._cache.processed_ids_for(provider, account)

    def begin_batch(self, provider: str, account: str) -> None:
        self._cache.begin_batch(provider, account)
        self._batch_provider = provider
        self._batch_account = account
        self._refresh()

    def mark_batch_processed(self, email_ids: list[str], last_date: datetime | None) -> None:
        """Backstop for IDs that weren't already cached with full detail by the
        classify+label path (e.g. failed label applies). Skips any ID already
        present so we don't clobber richer records."""
        provider = self._batch_provider or self.data["batch_state"].get("provider") or ""
        account = self._batch_account or self.data["batch_state"].get("account") or ""
        for eid in email_ids:
            if self._cache.is_processed(eid):
                continue
            self._cache.mark_processed(
                email_id=eid,
                provider=provider,
                account=account,
                category="",
                tags=[],
                confidence=0.0,
                subject="",
                sender="",
                email_date=last_date,
                run_id=None,
            )
        self._refresh()

    def end_batch(self) -> None:
        self._cache.end_batch()
        self._refresh()

    def reset_batch(self) -> None:
        provider = self._batch_provider or self.data["batch_state"].get("provider")
        account = self._batch_account or self.data["batch_state"].get("account")
        self._cache.reset_batch(provider, account)
        self._batch_provider = None
        self._batch_account = None
        self._refresh()
