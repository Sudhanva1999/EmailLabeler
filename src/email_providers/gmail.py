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
        self._label_prefix = os.getenv("LABEL_PREFIX", "AutoSort")
        self._service = None
        self._account = ""
        self._label_cache: dict[str, str] = {}

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
    ) -> Iterator[EmailMessage]:
        assert self._service is not None, "Call authenticate() first"
        query_parts: list[str] = []
        if since:
            query_parts.append(f"after:{int(since.timestamp())}")
        if until:
            query_parts.append(f"before:{int(until.timestamp())}")
        query = " ".join(query_parts)

        page_token = None
        while True:
            resp = self._service.users().messages().list(
                userId="me",
                q=query or None,
                pageToken=page_token,
                maxResults=100,
            ).execute()
            for ref in resp.get("messages", []):
                yield self._fetch_one(ref["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

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

    def delete_label(self, label_id: str) -> None:
        assert self._service is not None, "Call authenticate() first"
        self._service.users().labels().delete(userId="me", id=label_id).execute()

    def apply_labels(self, email_id: str, category: str, tags: list[str]) -> None:
        names = [f"{self._label_prefix}/{category}"]
        names.extend(f"{self._label_prefix}/tag/{t}" for t in tags)
        ids = [self._ensure_label(n) for n in names]
        self._service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": ids},
        ).execute()
