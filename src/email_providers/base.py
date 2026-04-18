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
    ) -> Iterator[EmailMessage]: ...

    @abstractmethod
    def apply_labels(self, email_id: str, category: str, tags: list[str]) -> None: ...
