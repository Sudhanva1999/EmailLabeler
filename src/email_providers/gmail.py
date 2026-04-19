import base64
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .base import EmailMessage, EmailProvider

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
OAUTH_PORT = 8080


class GmailProvider(EmailProvider):
    def __init__(self) -> None:
        self._creds_file = Path(os.getenv("GMAIL_CREDENTIALS_FILE", "credentials/gmail_credentials.json"))
        self._token_file = Path(os.getenv("GMAIL_TOKEN_FILE", "credentials/gmail_token.json"))
        self._label_prefix = os.getenv("LABEL_PREFIX", "")
        self._service = None
        self._account = ""
        self._label_cache: dict[str, str] = {}

    def _label_name(self, base: str) -> str:
        if self._label_prefix:
            return f"{self._label_prefix}/{base}"
        return base

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def account(self) -> str:
        return self._account

    def authenticate(self) -> None:
        creds = None
        if self._token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self._creds_file.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials file not found at {self._creds_file}. "
                        "Download it from Google Cloud Console → APIs & Services → Credentials."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self._creds_file), SCOPES)
                creds = flow.run_local_server(port=OAUTH_PORT)
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._token_file, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = self._service.users().getProfile(userId="me").execute()
        self._account = profile.get("emailAddress", "")

    def fetch_emails(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        order: str = "asc",
    ) -> Iterator[EmailMessage]:
        assert self._service is not None, "Call authenticate() first"
        query_parts: list[str] = []
        if since:
            query_parts.append(f"after:{int(since.timestamp())}")
        if until:
            query_parts.append(f"before:{int(until.timestamp())}")
        query = " ".join(query_parts)

        ids: list[str] = []
        page_token = None
        while True:
            resp = self._service.users().messages().list(
                userId="me",
                q=query or None,
                pageToken=page_token,
                maxResults=100,
            ).execute()
            for ref in resp.get("messages", []) or []:
                if order == "desc":
                    yield self._fetch_one(ref["id"])
                else:
                    ids.append(ref["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        for mid in reversed(ids):
            yield self._fetch_one(mid)

    def _fetch_one(self, message_id: str) -> EmailMessage:
        msg = self._service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        date_hdr = headers.get("date")
        try:
            date = parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(timezone.utc)
        except (TypeError, ValueError):
            date = datetime.now(timezone.utc)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        body = self._extract_body(msg.get("payload", {}))
        return EmailMessage(
            id=msg["id"],
            subject=subject,
            sender=sender,
            snippet=msg.get("snippet", ""),
            body=body,
            date=date,
            raw=msg,
        )

    def _extract_body(self, payload: dict) -> str:
        mime = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data")
        if data and mime.startswith("text/"):
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return ""
        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == "text/plain":
                text = self._extract_body(part)
                if text:
                    return text
        for part in payload.get("parts", []) or []:
            text = self._extract_body(part)
            if text:
                return text
        return ""

    def _ensure_label(self, name: str) -> str:
        if name in self._label_cache:
            return self._label_cache[name]
        labels = self._service.users().labels().list(userId="me").execute().get("labels", [])
        for lab in labels:
            self._label_cache[lab["name"]] = lab["id"]
        if name in self._label_cache:
            return self._label_cache[name]
        created = self._service.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        self._label_cache[name] = created["id"]
        return created["id"]

    def list_labels(self) -> list[dict]:
        """Return all user-created labels sorted by name."""
        assert self._service is not None, "Call authenticate() first"
        result = self._service.users().labels().list(userId="me").execute()
        return sorted(
            [l for l in result.get("labels", []) if l.get("type") == "user"],
            key=lambda l: l["name"].lower(),
        )

    def get_inbox_stats(self) -> dict:
        assert self._service is not None, "Call authenticate() first"
        profile = self._service.users().getProfile(userId="me").execute()

        system_label_ids = ["INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT"]
        folders: list[dict] = []
        inbox_total = inbox_unread = inbox_threads = inbox_threads_unread = 0
        for lid in system_label_ids:
            try:
                lab = self._service.users().labels().get(userId="me", id=lid).execute()
            except Exception:
                continue
            total = lab.get("messagesTotal", 0)
            unread = lab.get("messagesUnread", 0)
            folders.append({"name": lid.title(), "total": total, "unread": unread})
            if lid == "INBOX":
                inbox_total = total
                inbox_unread = unread
                inbox_threads = lab.get("threadsTotal", 0)
                inbox_threads_unread = lab.get("threadsUnread", 0)

        user_labels = self.list_labels()
        user_label_names = [l.get("name", "") for l in user_labels]

        return {
            "provider": "gmail",
            "account": self._account,
            "account_total_messages": profile.get("messagesTotal", 0),
            "account_total_threads": profile.get("threadsTotal", 0),
            "inbox_total": inbox_total,
            "inbox_unread": inbox_unread,
            "inbox_threads": inbox_threads,
            "inbox_threads_unread": inbox_threads_unread,
            "folders": folders,
            "user_labels": user_label_names,
        }

    def delete_label(self, label_id: str) -> None:
        assert self._service is not None, "Call authenticate() first"
        self._service.users().labels().delete(userId="me", id=label_id).execute()

    def apply_labels(self, email_id: str, category: str, tags: list[str]) -> None:
        names = [self._label_name(category)]
        names.extend(self._label_name(t) for t in tags)
        ids = [self._ensure_label(n) for n in names]
        self._service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": ids},
        ).execute()

    def replace_labels(
        self,
        email_id: str,
        old_category: str,
        old_tags: list[str],
        new_category: str,
        new_tags: list[str],
    ) -> None:
        old_names = [self._label_name(old_category)] + [self._label_name(t) for t in old_tags]
        new_names = [self._label_name(new_category)] + [self._label_name(t) for t in new_tags]
        to_add = [self._ensure_label(n) for n in new_names if n not in old_names]
        to_remove = [self._ensure_label(n) for n in old_names if n not in new_names]
        body: dict[str, list[str]] = {}
        if to_add:
            body["addLabelIds"] = to_add
        if to_remove:
            body["removeLabelIds"] = to_remove
        if not body:
            return
        self._service.users().messages().modify(
            userId="me",
            id=email_id,
            body=body,
        ).execute()
