import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests


@dataclass
class NotificationPayload:
    title: str
    body: str
    priority: str = field(default="normal")


class Notifier(ABC):
    @abstractmethod
    def send(self, payload: NotificationPayload) -> None: ...


class TelegramNotifier(Notifier):
    _API = "https://api.telegram.org"

    def __init__(self) -> None:
        self._token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not self._token or not self._chat_id:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set in .env"
            )

    def send(self, payload: NotificationPayload) -> None:
        text = f"<b>{payload.title}</b>\n\n{payload.body}"
        resp = requests.post(
            f"{self._API}/bot{self._token}/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data.get('description')}")


def get_notifier() -> Notifier | None:
    provider = (os.getenv("NOTIFY_PROVIDER") or "").strip().lower()
    if provider == "telegram":
        return TelegramNotifier()
    return None
