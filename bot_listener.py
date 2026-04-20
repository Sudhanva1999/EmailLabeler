#!/usr/bin/env python3
"""
EmailSorter Telegram command bot — managed by PM2 via ecosystem.config.js.

Uses Telegram long polling (getUpdates). Commands are hard-coded; no shell,
eval, or user-supplied code paths. Only whitelisted chat/user IDs are served.

Environment (in .env):
    TELEGRAM_BOT_TOKEN            — required
    TELEGRAM_CHAT_ID              — required (primary chat, always allowed)
    TELEGRAM_ALLOWED_CHAT_IDS     — optional, comma-separated extras
    TELEGRAM_ALLOWED_USER_IDS     — optional, comma-separated user IDs to accept
                                    (defense-in-depth for group chats)
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.accounts import build_provider, find_account, load_accounts
from src.batch_processor import BatchProcessor
from src.cache import Cache
from src.categorizer import Categorizer
from src.config import CATEGORIES_FILE, KEYWORD_ROUTES_FILE, ensure_env_file, load_env
from src.db import Database
from src.dropped_log import default_dropped_log
from src.keyword_router import KeywordRouter
from src.llm import get_llm_provider
from src.metadata import Metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

# ── hard limits ───────────────────────────────────────────────────────────────
MAX_MESSAGE_LEN = 512          # drop any inbound text longer than this
MAX_ARG_LEN = 64               # per-argument length cap
MAX_ARGS = 4                   # refuse commands with more than this many args
POLL_TIMEOUT = 30              # long-polling timeout (seconds)
ARG_RE = re.compile(r"^[A-Za-z0-9_.@+\-\s]{1,%d}$" % MAX_ARG_LEN)
FALLBACK_WINDOW_HOURS = 25

_run_lock = threading.Lock()   # ensures /run cannot overlap itself


# ── auth ──────────────────────────────────────────────────────────────────────

def _parse_id_csv(value: str) -> set[int]:
    out: set[int] = set()
    for piece in (value or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except ValueError:
            log.warning("Ignoring non-integer id in allowlist: %r", piece)
    return out


def load_allowed_chat_ids() -> set[int]:
    ids = _parse_id_csv(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    primary = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if primary:
        try:
            ids.add(int(primary))
        except ValueError:
            log.error("TELEGRAM_CHAT_ID is not an integer — bot will refuse ALL chats")
    return ids


def load_allowed_user_ids() -> set[int]:
    return _parse_id_csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))


# ── telegram transport ───────────────────────────────────────────────────────

class Telegram:
    def __init__(self, token: str) -> None:
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        self._base = f"https://api.telegram.org/bot{token}"
        self._session = requests.Session()

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        try:
            resp = self._session.get(
                f"{self._base}/getUpdates",
                params={
                    "timeout": timeout,
                    "offset": offset,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=timeout + 5,
            )
        except requests.RequestException as exc:
            log.warning("getUpdates transport error: %s", exc)
            time.sleep(3)
            return []
        if resp.status_code != 200:
            log.warning("getUpdates HTTP %s: %s", resp.status_code, resp.text[:200])
            time.sleep(3)
            return []
        data = resp.json()
        return data.get("result", []) if data.get("ok") else []

    def send(self, chat_id: int, text: str) -> None:
        try:
            self._session.post(
                f"{self._base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            log.warning("sendMessage error: %s", exc)


# ── command handlers ─────────────────────────────────────────────────────────

def _h(value: Any) -> str:
    """HTML-escape arbitrary content before sending to Telegram."""
    return html.escape(str(value), quote=False)


def _parse_int(arg: str | None, default: int, lo: int, hi: int) -> int:
    if arg is None:
        return default
    try:
        n = int(arg)
    except ValueError:
        return default
    return max(lo, min(hi, n))


def _account_or_error(accounts: list[dict], query: str | None) -> tuple[dict | None, str | None]:
    if not query:
        return None, "Usage: pass an account label or email address."
    acc = find_account(accounts, query)
    if not acc:
        labels = ", ".join(_h(a.get("label", "?")) for a in accounts)
        return None, f"No account matching <code>{_h(query)}</code>. Known: {labels}"
    return acc, None


def cmd_help(args: list[str], ctx: dict) -> str:
    return (
        "<b>EmailSorter bot</b>\n\n"
        "<code>/accounts</code> — list configured accounts\n"
        "<code>/inbox &lt;label|email&gt;</code> — live inbox stats\n"
        "<code>/tags &lt;label|email&gt; [days=7]</code> — tag counts\n"
        "<code>/cats &lt;label|email&gt; [days=7]</code> — category counts\n"
        "<code>/last &lt;label|email&gt; [n=5]</code> — recent classified\n"
        "<code>/dropped [n=5]</code> — recent dropped emails\n"
        "<code>/status</code> — last run per account\n"
        "<code>/run [label|email]</code> — trigger a run"
    )


def cmd_accounts(args: list[str], ctx: dict) -> str:
    accounts = ctx["accounts"]
    if not accounts:
        return "No accounts configured."
    lines = ["<b>Accounts</b>"]
    cache: Cache = ctx["cache"]
    for a in accounts:
        label = _h(a.get("label", "?"))
        prov = _h(a.get("provider", "?"))
        last = cache.last_run_for(prov, a.get("label", ""))
        email = _h(a.get("email") or (last.account if last else ""))
        extra = f" · {email}" if email else ""
        lines.append(f"• <b>{label}</b> ({prov}){extra}")
    return "\n".join(lines)


def cmd_inbox(args: list[str], ctx: dict) -> str:
    acc, err = _account_or_error(ctx["accounts"], args[0] if args else None)
    if err:
        return err
    provider = build_provider(acc)
    provider.authenticate()
    stats = provider.get_inbox_stats()
    lines = [
        f"<b>📊 {_h(stats.get('account', ''))}</b> ({_h(stats.get('provider', ''))})",
        f"Inbox total:  <b>{stats.get('inbox_total', 0)}</b>",
        f"Inbox unread: <b>{stats.get('inbox_unread', 0)}</b>",
    ]
    folders = stats.get("folders", [])[:10]
    if folders:
        col_w = max(len(f["name"]) for f in folders) + 1
        rows = "\n".join(
            f"{_h(f['name']):<{col_w}} {f['total']:>5}"
            + (f"  ({f['unread']} unread)" if f["unread"] else "")
            for f in folders
        )
        lines.append(f"<code>{rows}</code>")
    return "\n".join(lines)


def cmd_tags(args: list[str], ctx: dict) -> str:
    acc, err = _account_or_error(ctx["accounts"], args[0] if args else None)
    if err:
        return err
    days = _parse_int(args[1] if len(args) > 1 else None, 7, 1, 365)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts = ctx["cache"].tag_counts_for(acc["provider"], acc.get("label", ""), since)
    if not counts:
        return f"No tags in last {days} day(s) for {_h(acc.get('label', ''))}."
    col_w = max(len(t) for t, _ in counts) + 1
    body = "\n".join(f"{_h(t):<{col_w}} {c:>3}" for t, c in counts[:25])
    return f"<b>Tags · {_h(acc.get('label', ''))} · {days}d</b>\n<code>{body}</code>"


def cmd_cats(args: list[str], ctx: dict) -> str:
    acc, err = _account_or_error(ctx["accounts"], args[0] if args else None)
    if err:
        return err
    days = _parse_int(args[1] if len(args) > 1 else None, 7, 1, 365)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts = ctx["cache"].category_counts_for(acc["provider"], acc.get("label", ""), since)
    if not counts:
        return f"No categories in last {days} day(s) for {_h(acc.get('label', ''))}."
    col_w = max(len(c) for c, _ in counts) + 1
    body = "\n".join(f"{_h(c):<{col_w}} {n:>3}" for c, n in counts)
    return f"<b>Categories · {_h(acc.get('label', ''))} · {days}d</b>\n<code>{body}</code>"


def cmd_last(args: list[str], ctx: dict) -> str:
    acc, err = _account_or_error(ctx["accounts"], args[0] if args else None)
    if err:
        return err
    n = _parse_int(args[1] if len(args) > 1 else None, 5, 1, 20)
    rows = ctx["cache"].recent_processed_for(acc["provider"], acc.get("label", ""), n)
    if not rows:
        return f"No processed emails for {_h(acc.get('label', ''))}."
    lines = [f"<b>Last {n} · {_h(acc.get('label', ''))}</b>"]
    for r in rows:
        tags = ", ".join(r["tags"][:4])
        subj = r["subject"][:60]
        lines.append(f"• <b>{_h(r['category'])}</b>  {_h(subj)}\n  <i>{_h(tags)}</i>")
    return "\n".join(lines)


def cmd_dropped(args: list[str], ctx: dict) -> str:
    n = _parse_int(args[0] if args else None, 5, 1, 20)
    path = Path(os.getenv("DROPPED_LOG_FILE", "dropped_emails.jsonl"))
    if not path.exists():
        return "No dropped emails log yet."
    try:
        entries = path.read_text(encoding="utf-8").strip().splitlines()[-n:]
    except OSError as exc:
        return f"Couldn't read dropped log: {_h(exc)}"
    if not entries:
        return "No dropped emails."
    lines = [f"<b>Last {len(entries)} dropped</b>"]
    for line in entries:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        subj = (e.get("subject") or "")[:60]
        err = (e.get("error") or "")[:120]
        lines.append(f"• {_h(subj)}\n  <i>{_h(err)}</i>")
    return "\n".join(lines)


def cmd_status(args: list[str], ctx: dict) -> str:
    cache: Cache = ctx["cache"]
    lines = ["<b>Status</b>"]
    for a in ctx["accounts"]:
        label = a.get("label", "")
        last = cache.last_run_for(a["provider"], label)
        if last:
            when = last.timestamp.split("T")[0]
            lines.append(
                f"• <b>{_h(label)}</b> — last {_h(when)} "
                f"· {last.emails_processed} processed · mode={_h(last.mode)}"
            )
        else:
            lines.append(f"• <b>{_h(label)}</b> — no runs yet")
    return "\n".join(lines)


def cmd_run(args: list[str], ctx: dict) -> str:
    target: list[dict]
    if args:
        acc, err = _account_or_error(ctx["accounts"], args[0])
        if err:
            return err
        target = [acc]
    else:
        target = list(ctx["accounts"])

    if not _run_lock.acquire(blocking=False):
        return "⚠️ A run is already in progress. Try again later."

    try:
        total = 0
        errors: list[str] = []
        router = ctx["router"]
        llm = ctx["llm"]
        until = datetime.now(timezone.utc)
        dropped = default_dropped_log()
        for a in target:
            label = a.get("label", a["provider"])
            try:
                provider = build_provider(a)
                provider.authenticate()
                cache = Cache(Database())
                raw = cache.last_processed_date_for(provider.name, provider.account)
                if raw:
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    since = dt
                else:
                    since = until - timedelta(hours=FALLBACK_WINDOW_HOURS)
                metadata = Metadata(cache=cache)
                categorizer = Categorizer(CATEGORIES_FILE, llm, router=router)
                proc = BatchProcessor(provider, categorizer, metadata, dropped_log=dropped)
                results = proc.run_range(since, until)
                total += len(results)
            except Exception as exc:
                log.error("/run failed for %s: %s", label, exc, exc_info=True)
                errors.append(f"{label}: {exc}")
        msg = f"✅ Run complete — {total} emails across {len(target)} account(s)."
        if errors:
            msg += "\n\n⚠️ " + "\n⚠️ ".join(_h(e) for e in errors)
        return msg
    finally:
        _run_lock.release()


HANDLERS: dict[str, Callable[[list[str], dict], str]] = {
    "help": cmd_help,
    "start": cmd_help,
    "accounts": cmd_accounts,
    "inbox": cmd_inbox,
    "tags": cmd_tags,
    "cats": cmd_cats,
    "last": cmd_last,
    "dropped": cmd_dropped,
    "status": cmd_status,
    "run": cmd_run,
}


# ── message parsing ──────────────────────────────────────────────────────────

def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Strictly parse `/cmd arg1 arg2` — reject anything else."""
    if not text or not text.startswith("/"):
        return None
    if len(text) > MAX_MESSAGE_LEN:
        return None
    body = text[1:].strip()
    if not body:
        return None
    parts = body.split()
    head = parts[0].split("@", 1)[0].lower()
    if not head.isalpha() or len(head) > 32:
        return None
    args = parts[1:MAX_ARGS + 1]
    for a in args:
        if not ARG_RE.match(a):
            return None
    return head, args


# ── main loop ────────────────────────────────────────────────────────────────

def build_context() -> dict:
    accounts = load_accounts()
    with open(CATEGORIES_FILE, encoding="utf-8") as fh:
        cat_data = json.load(fh)
    routes_path = os.getenv("KEYWORD_ROUTES_FILE", str(KEYWORD_ROUTES_FILE))
    router = KeywordRouter(
        routes_path,
        valid_categories=list(cat_data["primary_categories"].keys()),
        valid_tags=list(cat_data["tags"].keys()),
    )
    return {
        "accounts": accounts,
        "cache": Cache(Database()),
        "router": router,
        "llm": get_llm_provider(),
    }


def handle_update(update: dict, tg: Telegram, ctx: dict,
                  allowed_chats: set[int], allowed_users: set[int]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or chat_id not in allowed_chats:
        log.warning("Ignoring message from unauthorized chat_id=%s user_id=%s", chat_id, user_id)
        return
    if allowed_users and user_id not in allowed_users:
        log.warning("Ignoring user not in allowlist: user_id=%s chat_id=%s", user_id, chat_id)
        return

    parsed = parse_command(text)
    if not parsed:
        return  # silently ignore anything that isn't a well-formed command

    cmd, args = parsed
    handler = HANDLERS.get(cmd)
    if not handler:
        return  # unknown command → ignore (do not leak command list)

    log.info("cmd=/%s args=%s chat=%s user=%s", cmd, args, chat_id, user_id)
    try:
        reply = handler(args, ctx)
    except Exception as exc:
        log.error("Handler /%s failed: %s", cmd, exc, exc_info=True)
        reply = f"⚠️ Command failed: {_h(exc)}"
    tg.send(chat_id, reply)


def main() -> int:
    ensure_env_file()
    load_env()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.error("TELEGRAM_BOT_TOKEN not set — refusing to start")
        return 1

    allowed_chats = load_allowed_chat_ids()
    if not allowed_chats:
        log.error("No allowed chat ids configured — refusing to start")
        return 1
    allowed_users = load_allowed_user_ids()

    log.info("Bot starting. Allowed chats=%s user-lock=%s",
             sorted(allowed_chats), bool(allowed_users))

    tg = Telegram(token)
    ctx = build_context()
    offset: int | None = None

    while True:
        updates = tg.get_updates(offset, POLL_TIMEOUT)
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                handle_update(upd, tg, ctx, allowed_chats, allowed_users)
            except Exception as exc:
                log.error("Unhandled error in update loop: %s", exc, exc_info=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
