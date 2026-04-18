import os

from .base import EmailMessage, EmailProvider


def get_email_provider() -> EmailProvider:
    provider = os.getenv("EMAIL_PROVIDER", "gmail").strip().lower()
    if provider == "gmail":
        from .gmail import GmailProvider
        return GmailProvider()
    if provider == "outlook":
        from .outlook import OutlookProvider
        return OutlookProvider()
    raise ValueError(f"Unknown EMAIL_PROVIDER: {provider!r} (expected 'gmail' or 'outlook')")


__all__ = ["EmailMessage", "EmailProvider", "get_email_provider"]
