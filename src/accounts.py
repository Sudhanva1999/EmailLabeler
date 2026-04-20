"""Shared account loading + provider factory used by daily_run.py and bot_listener.py."""
import json
import os
from pathlib import Path

from .email_providers.base import EmailProvider
from .email_providers.gmail import GmailProvider
from .email_providers.outlook import OutlookProvider

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_FILE = ROOT / "accounts.json"


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


def build_provider(account_cfg: dict) -> EmailProvider:
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


def find_account(accounts: list[dict], query: str) -> dict | None:
    """Resolve an account by label or email (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for acc in accounts:
        if acc.get("label", "").lower() == q:
            return acc
    for acc in accounts:
        if acc.get("email", "").lower() == q:
            return acc
    return None
