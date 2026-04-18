"""Pre-LLM cleanup. Removes HTML, scripts, URLs, quoted replies, and noise so
local models receive compact plain-text input."""

import re
from html import unescape

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}\b")
_TRACKING_RE = re.compile(r"\[image:[^\]]*\]", re.IGNORECASE)
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_INLINE_WS_RE = re.compile(r"[ \t]+")


def normalize_subject(s: str | None) -> str:
    if not s:
        return ""
    s = unescape(s)
    s = _ZERO_WIDTH_RE.sub("", s)
    return _INLINE_WS_RE.sub(" ", s).strip()


def normalize_body(body: str | None, max_chars: int = 4000) -> str:
    """Strip HTML, scripts, URLs, quoted reply chains, and base64 blobs.

    Output is plain text with whitespace collapsed and bounded by `max_chars`.
    """
    if not body:
        return ""

    text = body
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _URL_RE.sub("[link]", text)
    text = _BASE64_RE.sub("[blob]", text)
    text = _TRACKING_RE.sub("", text)

    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append("")
            continue
        if stripped.startswith(">"):
            continue
        if stripped.lower().startswith(("on ", "from:", "sent:", "subject:", "to:", "cc:")) and len(stripped) > 60:
            continue
        kept_lines.append(_INLINE_WS_RE.sub(" ", stripped))

    text = "\n".join(kept_lines)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return text
