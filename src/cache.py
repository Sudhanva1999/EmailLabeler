import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    id: int
    timestamp: str
    provider: str
    account: str
    emails_processed: int
    mode: str
    date_from: str | None
    date_to: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "provider": self.provider,
            "account": self.account,
            "emails_processed": self.emails_processed,
            "mode": self.mode,
            "date_range": {"from": self.date_from, "to": self.date_to},
        }


class Cache:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    # --- runs ---

    def record_run(
        self,
        provider: str,
        account: str,
        emails_processed: int,
        date_from: datetime | None,
        date_to: datetime | None,
        mode: str,
    ) -> int:
        cur = self.db.conn.execute(
            """
            INSERT INTO runs (timestamp, provider, account, emails_processed, mode, date_from, date_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now_iso(),
                provider,
                account,
                emails_processed,
                mode,
                date_from.isoformat() if date_from else None,
                date_to.isoformat() if date_to else None,
            ),
        )
        return int(cur.lastrowid)

    def last_run(self) -> RunRecord | None:
        row = self.db.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self._row_to_run(row) if row else None

    def recent_runs(self, limit: int = 50) -> list[RunRecord]:
        rows = self.db.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    @staticmethod
    def _row_to_run(row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            provider=row["provider"] or "",
            account=row["account"] or "",
            emails_processed=row["emails_processed"] or 0,
            mode=row["mode"] or "",
            date_from=row["date_from"],
            date_to=row["date_to"],
        )

    # --- processed emails ---

    def is_processed(self, email_id: str) -> bool:
        row = self.db.conn.execute(
            "SELECT 1 FROM processed_emails WHERE email_id = ? LIMIT 1", (email_id,)
        ).fetchone()
        return row is not None

    def mark_processed(
        self,
        email_id: str,
        provider: str,
        account: str,
        category: str,
        tags: list[str],
        confidence: float,
        subject: str,
        sender: str,
        email_date: datetime | None,
        run_id: int | None,
    ) -> None:
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO processed_emails
            (email_id, run_id, provider, account, category, tags, confidence,
             classified_at, email_date, subject, sender)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email_id,
                run_id,
                provider,
                account,
                category,
                json.dumps(tags or []),
                confidence,
                _now_iso(),
                email_date.isoformat() if email_date else None,
                subject,
                sender,
            ),
        )

    def processed_ids_for(self, provider: str, account: str) -> set[str]:
        rows = self.db.conn.execute(
            "SELECT email_id FROM processed_emails WHERE provider = ? AND account = ?",
            (provider, account),
        ).fetchall()
        return {r["email_id"] for r in rows}

    def last_processed_date_for(self, provider: str, account: str) -> str | None:
        row = self.db.conn.execute(
            """
            SELECT email_date FROM processed_emails
            WHERE provider = ? AND account = ? AND email_date IS NOT NULL
            ORDER BY email_date DESC LIMIT 1
            """,
            (provider, account),
        ).fetchone()
        return row["email_date"] if row else None

    def purge_processed_for(self, provider: str, account: str) -> int:
        cur = self.db.conn.execute(
            "DELETE FROM processed_emails WHERE provider = ? AND account = ?",
            (provider, account),
        )
        return cur.rowcount or 0

    # --- keyword hits ---

    def record_route_hit(self, email_id: str, rule_name: str) -> None:
        self.db.conn.execute(
            "INSERT INTO keyword_route_hits (email_id, rule_name, classified_at) VALUES (?, ?, ?)",
            (email_id, rule_name, _now_iso()),
        )

    # --- config_state (singleton key-value) ---

    def set_state(self, key: str, value: str | None) -> None:
        if value is None:
            self.db.conn.execute("DELETE FROM config_state WHERE key = ?", (key,))
            return
        self.db.conn.execute(
            "INSERT OR REPLACE INTO config_state (key, value) VALUES (?, ?)",
            (key, value),
        )

    def get_state(self, key: str) -> str | None:
        row = self.db.conn.execute(
            "SELECT value FROM config_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # --- batch state helpers (built on config_state + processed_emails) ---

    def begin_batch(self, provider: str, account: str) -> None:
        if self.get_state("batch.active") == "true":
            return
        self.set_state("batch.active", "true")
        self.set_state("batch.started_at", _now_iso())
        self.set_state("batch.provider", provider)
        self.set_state("batch.account", account)

    def end_batch(self) -> None:
        self.set_state("batch.active", "false")

    def reset_batch(self, provider: str | None = None, account: str | None = None) -> None:
        """Reset batch bookkeeping. If provider+account are given, also drop the
        processed email records for that account so it restarts fresh."""
        for key in ("batch.active", "batch.started_at", "batch.provider", "batch.account"):
            self.set_state(key, None)
        if provider and account:
            self.purge_processed_for(provider, account)

    def batch_state_dict(self) -> dict[str, Any]:
        active = self.get_state("batch.active") == "true"
        provider = self.get_state("batch.provider")
        account = self.get_state("batch.account")
        last_date = None
        if provider and account:
            last_date = self.last_processed_date_for(provider, account)
        ids: list[str] = []
        if provider and account:
            rows = self.db.conn.execute(
                "SELECT email_id FROM processed_emails WHERE provider = ? AND account = ?",
                (provider, account),
            ).fetchall()
            ids = [r["email_id"] for r in rows]
        return {
            "active": active,
            "started_at": self.get_state("batch.started_at"),
            "completed_ids": ids,
            "last_processed_date": last_date,
            "provider": provider,
            "account": account,
        }
