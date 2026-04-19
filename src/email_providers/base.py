from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator


@dataclass
class EmailMessage:
    id: str
    subject: str
    sender: str
    snippet: str
    body: str
    date: datetime
    raw: dict = field(default_factory=dict)


class EmailProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def account(self) -> str: ...

    @abstractmethod
    def authenticate(self) -> None: ...

    @abstractmethod
    def fetch_emails(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        order: str = "asc",
    ) -> Iterator[EmailMessage]:
        """Yield messages in the chosen chronological order.
        order="asc"  → oldest first (default)
        order="desc" → newest first
        """

    @abstractmethod
    def apply_labels(self, email_id: str, category: str, tags: list[str]) -> None: ...

    @abstractmethod
    def get_inbox_stats(self) -> dict:
        """Return metadata about the mailbox. Shape varies by provider but
        always includes 'provider', 'account', 'inbox_total', 'inbox_unread',
        and 'folders' (list of dicts with name/total/unread keys)."""

    def replace_labels(
        self,
        email_id: str,
        old_category: str,
        old_tags: list[str],
        new_category: str,
        new_tags: list[str],
    ) -> None:
        """Swap old labels for new ones on a message. Default naive impl:
        remove old (no-op here), then apply new. Providers should override
        when the backend supports atomic add/remove semantics."""
        self.apply_labels(email_id, new_category, new_tags)
