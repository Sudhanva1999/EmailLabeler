import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import msal
import requests

from .base import EmailMessage, EmailProvider

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite"]


class OutlookProvider(EmailProvider):
    def __init__(self) -> None:
        self._client_id = os.getenv("OUTLOOK_CLIENT_ID", "")
        self._tenant = os.getenv("OUTLOOK_TENANT_ID", "common")
        self._token_file = Path(os.getenv("OUTLOOK_TOKEN_FILE", "credentials/outlook_token.json"))
        self._label_prefix = os.getenv("LABEL_PREFIX", "")
        self._account = ""
        self._access_token: str | None = None
        self._app: msal.PublicClientApplication | None = None
        self._category_cache: set[str] = set()

    def _label_name(self, base: str) -> str:
        if self._label_prefix:
            return f"{self._label_prefix}/{base}"
        return base

    @property
    def name(self) -> str:
        return "outlook"

    @property
    def account(self) -> str:
        return self._account

    def authenticate(self) -> None:
        if not self._client_id:
            raise RuntimeError("OUTLOOK_CLIENT_ID is not set")
        cache = msal.SerializableTokenCache()
        if self._token_file.exists():
            cache.deserialize(self._token_file.read_text(encoding="utf-8"))

        self._app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=f"https://login.microsoftonline.com/{self._tenant}",
            token_cache=cache,
        )

        result = None
        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result:
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Failed to start device flow: {json.dumps(flow, indent=2)}")
            print(flow["message"])
            result = self._app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(f"Outlook auth failed: {result.get('error_description')}")

        self._access_token = result["access_token"]
        if cache.has_state_changed:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            self._token_file.write_text(cache.serialize(), encoding="utf-8")

        me = self._get(f"{GRAPH}/me")
        self._account = me.get("userPrincipalName") or me.get("mail") or ""

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = requests.get(url, headers=self._headers(), params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def fetch_emails(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        order: str = "asc",
    ) -> Iterator[EmailMessage]:
        filters: list[str] = []
        if since:
            filters.append(f"receivedDateTime ge {since.astimezone(timezone.utc).isoformat().replace('+00:00','Z')}")
        if until:
            filters.append(f"receivedDateTime lt {until.astimezone(timezone.utc).isoformat().replace('+00:00','Z')}")

        direction = "desc" if order == "desc" else "asc"
        params = {
            "$top": "50",
            "$select": "id,subject,from,bodyPreview,body,receivedDateTime,categories",
            "$orderby": f"receivedDateTime {direction}",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        url = f"{GRAPH}/me/messages"
        while url:
            data = self._get(url, params=params if url.endswith("/messages") else None)
            for item in data.get("value", []):
                yield self._to_message(item)
            url = data.get("@odata.nextLink")
            params = None

    def _to_message(self, item: dict) -> EmailMessage:
        sender = ""
        from_field = item.get("from") or {}
        addr = from_field.get("emailAddress") or {}
        if addr:
            sender = f"{addr.get('name', '')} <{addr.get('address', '')}>".strip()
        body_obj = item.get("body") or {}
        body_text = body_obj.get("content", "") if body_obj.get("contentType") == "text" else ""
        date_str = item.get("receivedDateTime")
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")) if date_str else datetime.now(timezone.utc)
        except ValueError:
            date = datetime.now(timezone.utc)
        return EmailMessage(
            id=item["id"],
            subject=item.get("subject", ""),
            sender=sender,
            snippet=item.get("bodyPreview", ""),
            body=body_text or item.get("bodyPreview", ""),
            date=date,
            raw=item,
        )

    def get_inbox_stats(self) -> dict:
        assert self._access_token is not None, "Call authenticate() first"
        inbox = self._get(
            f"{GRAPH}/me/mailFolders/inbox",
            params={"$select": "displayName,totalItemCount,unreadItemCount,childFolderCount"},
        )
        folders_data = self._get(
            f"{GRAPH}/me/mailFolders",
            params={"$top": "50", "$select": "displayName,totalItemCount,unreadItemCount"},
        )
        folders = [
            {
                "name": f.get("displayName", ""),
                "total": f.get("totalItemCount", 0),
                "unread": f.get("unreadItemCount", 0),
            }
            for f in folders_data.get("value", [])
        ]
        return {
            "provider": "outlook",
            "account": self._account,
            "inbox_total": inbox.get("totalItemCount", 0),
            "inbox_unread": inbox.get("unreadItemCount", 0),
            "inbox_child_folders": inbox.get("childFolderCount", 0),
            "folders": folders,
            "user_labels": [],
        }

    def _current_categories(self, email_id: str) -> list[str]:
        data = self._get(f"{GRAPH}/me/messages/{email_id}", params={"$select": "categories"})
        return list(data.get("categories") or [])

    def apply_labels(self, email_id: str, category: str, tags: list[str]) -> None:
        managed = {self._label_name(category), *(self._label_name(t) for t in tags)}
        existing = self._current_categories(email_id)
        merged = list(dict.fromkeys([*existing, *managed]))
        self._patch_categories(email_id, merged)

    def replace_labels(
        self,
        email_id: str,
        old_category: str,
        old_tags: list[str],
        new_category: str,
        new_tags: list[str],
    ) -> None:
        old_names = {self._label_name(old_category), *(self._label_name(t) for t in old_tags)}
        new_names = {self._label_name(new_category), *(self._label_name(t) for t in new_tags)}
        existing = self._current_categories(email_id)
        remaining = [c for c in existing if c not in old_names or c in new_names]
        merged = list(dict.fromkeys([*remaining, *new_names]))
        self._patch_categories(email_id, merged)

    def _patch_categories(self, email_id: str, categories: list[str]) -> None:
        r = requests.patch(
            f"{GRAPH}/me/messages/{email_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"categories": categories},
            timeout=60,
        )
        r.raise_for_status()
