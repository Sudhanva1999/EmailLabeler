import os
import sqlite3
import threading
from pathlib import Path

from .config import ROOT


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    provider TEXT,
    account TEXT,
    emails_processed INTEGER,
    mode TEXT,
    date_from TEXT,
    date_to TEXT
);

CREATE TABLE IF NOT EXISTS processed_emails (
    email_id TEXT PRIMARY KEY,
    run_id INTEGER,
    provider TEXT,
    account TEXT,
    category TEXT,
    tags TEXT,
    confidence REAL,
    classified_at TEXT,
    email_date TEXT,
    subject TEXT,
    sender TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_processed_account ON processed_emails(account);
CREATE INDEX IF NOT EXISTS idx_processed_category ON processed_emails(category);
CREATE INDEX IF NOT EXISTS idx_processed_run ON processed_emails(run_id);

CREATE TABLE IF NOT EXISTS keyword_route_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT,
    rule_name TEXT,
    classified_at TEXT
);

CREATE TABLE IF NOT EXISTS config_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def default_db_path() -> Path:
    configured = os.getenv("DB_FILE", "email_sorter.db")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    return path


_lock = threading.Lock()


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with _lock:
            self._conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()
