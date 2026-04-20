#!/usr/bin/env python3
"""
EmailSorter daily cron job — managed by PM2 via ecosystem.config.js.

Runs the labeller for every account in accounts.json (falls back to the
single-account .env config when the file is absent).  Sends Telegram
notifications: one inbox-stats message per account, then a combined
classification summary across all accounts.

Time window: uses cache.last_processed_date_for(provider, account) so each
account independently tracks where it left off.  Falls back to now-25h on
the very first run for an account.
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.batch_processor import BatchProcessor, ProcessResult
from src.cache import Cache
from src.categorizer import Categorizer
from src.config import CATEGORIES_FILE, KEYWORD_ROUTES_FILE, ensure_env_file, load_env
from src.db import Database
from src.dropped_log import default_dropped_log
from src.email_providers.gmail import GmailProvider
from src.email_providers.outlook import OutlookProvider
from src.keyword_router import KeywordRouter
from src.llm import get_llm_provider
from src.metadata import Metadata
from src.notifier import NotificationPayload, get_notifier
from src.summarizer import build_inbox_summary, build_run_summary

ACCOUNTS_FILE = ROOT / "accounts.json"
FALLBACK_WINDOW_HOURS = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily_run")


# ── account loading ───────────────────────────────────────────────────────────

def load_accounts() -> list[dict]:
    if ACCOUNTS_FILE.exists():
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        accounts = data.get("accounts", [])
        if accounts:
            return accounts

    provider = os.getenv("EMAIL_PROVIDER", "gmail")
    if provider == "gmail":
        return [{
            "provider": "gmail",
            "label": os.getenv("EMAIL_ACCOUNT", "Gmail"),
            "credentials_file": os.getenv("GMAIL_CREDENTIALS_FILE", "credentials/gmail_credentials.json"),
            "token_file": os.getenv("GMAIL_TOKEN_FILE", "credentials/gmail_token.json"),
        }]
    if provider == "outlook":
        return [{
            "provider": "outlook",
            "label": os.getenv("EMAIL_ACCOUNT", "Outlook"),
            "token_file": os.getenv("OUTLOOK_TOKEN_FILE", "credentials/outlook_token.json"),
        }]
    return []


# ── provider factory ──────────────────────────────────────────────────────────

def build_provider(account_cfg: dict):
    name = account_cfg["provider"]
    if name == "gmail":
        p = GmailProvider()
        p._creds_file = ROOT / account_cfg.get("credentials_file", "credentials/gmail_credentials.json")
        p._token_file = ROOT / account_cfg.get("token_file", "credentials/gmail_token.json")
        return p
    if name == "outlook":
        p = OutlookProvider()
        p._token_file = ROOT / account_cfg.get("token_file", "credentials/outlook_token.json")
        if "client_id" in account_cfg:
            p._client_id = account_cfg["client_id"]
        return p
    raise ValueError(f"Unknown provider: {name!r}")


# ── per-account time window ───────────────────────────────────────────────────

def get_since(cache: Cache, provider_name: str, account: str, until: datetime) -> datetime:
    raw = cache.last_processed_date_for(provider_name, account)
    if raw:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    fallback = until - timedelta(hours=FALLBACK_WINDOW_HOURS)
    log.info("    No prior run found — falling back to %s", fallback.isoformat())
    return fallback


# ── single-account processing ─────────────────────────────────────────────────

def run_account(
    account_cfg: dict,
    until: datetime,
    router: KeywordRouter,
    llm,
) -> tuple[list[ProcessResult], dict | None]:
    label = account_cfg.get("label", account_cfg["provider"])
    log.info("  Authenticating: %s", label)

    provider = build_provider(account_cfg)
    provider.authenticate()
    log.info("  Signed in as: %s", provider.account)

    cache = Cache(Database())
    since = get_since(cache, provider.name, provider.account, until)
    log.info("  Window: %s → %s", since.date(), until.date())

    metadata = Metadata(cache=cache)
    dropped = default_dropped_log()
    categorizer = Categorizer(CATEGORIES_FILE, llm, router=router)
    proc = BatchProcessor(provider, categorizer, metadata, dropped_log=dropped)

    results = proc.run_range(since, until)
    log.info("  Classified: %d emails", len(results))

    inbox_stats = None
    try:
        inbox_stats = provider.get_inbox_stats()
    except Exception as exc:
        log.warning("  Inbox stats unavailable: %s", exc)

    return results, inbox_stats


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    ensure_env_file()
    load_env()

    accounts = load_accounts()
    if not accounts:
        log.error("No accounts configured. Create accounts.json or set EMAIL_PROVIDER in .env")
        return 1

    log.info("=== EmailSorter daily run — %d account(s) ===", len(accounts))

    with open(CATEGORIES_FILE, encoding="utf-8") as fh:
        cat_data = json.load(fh)

    routes_path = os.getenv("KEYWORD_ROUTES_FILE", str(KEYWORD_ROUTES_FILE))
    router = KeywordRouter(
        routes_path,
        valid_categories=list(cat_data["primary_categories"].keys()),
        valid_tags=list(cat_data["tags"].keys()),
    )

    llm = get_llm_provider()
    notifier = get_notifier()
    until = datetime.now(timezone.utc)

    all_results: list[ProcessResult] = []
    errors: list[str] = []

    for idx, account_cfg in enumerate(accounts, 1):
        log.info("[%d/%d] %s / %s", idx, len(accounts), account_cfg["provider"], account_cfg.get("label", ""))
        try:
            results, inbox_stats = run_account(account_cfg, until, router, llm)
            all_results.extend(results)

            if notifier and inbox_stats:
                notifier.send(build_inbox_summary(inbox_stats))
                log.info("  Inbox stats notification sent")

        except Exception as exc:
            msg = f"{account_cfg.get('label', account_cfg['provider'])}: {exc}"
            log.error("  FAILED — %s", msg, exc_info=True)
            errors.append(msg)
            if notifier:
                notifier.send(NotificationPayload(
                    title="❌ EmailSorter — Account Error",
                    body=f"<code>{msg}</code>",
                ))

    if notifier:
        payload = build_run_summary(all_results, mode="default")
        if errors:
            payload.body += "\n\n" + "\n".join(f"⚠️ {e}" for e in errors)
        notifier.send(payload)
        log.info("Combined summary notification sent")

    log.info("=== Done — %d emails across %d account(s) ===", len(all_results), len(accounts))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
