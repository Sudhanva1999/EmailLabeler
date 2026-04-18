import os
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CATEGORIES_FILE = ROOT / "categories.json"
METADATA_FILE = ROOT / "metadata.json"

ALLOWED_KEYS = {
    "EMAIL_PROVIDER",
    "EMAIL_ACCOUNT",
    "GMAIL_CREDENTIALS_FILE",
    "GMAIL_TOKEN_FILE",
    "OUTLOOK_CLIENT_ID",
    "OUTLOOK_TENANT_ID",
    "OUTLOOK_CLIENT_SECRET",
    "OUTLOOK_TOKEN_FILE",
    "LLM_PROVIDER",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "LOCAL_LLM_BASE_URL",
    "LOCAL_LLM_MODEL",
    "LOCAL_LLM_API_KEY",
    "BATCH_SIZE",
    "LABEL_PREFIX",
    "MAX_CLASSIFY_RETRIES",
    "BODY_CHAR_LIMIT",
    "DROPPED_LOG_FILE",
}

SECRET_KEYS = {"GEMINI_API_KEY", "OUTLOOK_CLIENT_SECRET", "LOCAL_LLM_API_KEY"}


def load_env() -> None:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=True)


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        return
    example = ROOT / ".env.example"
    if example.exists():
        ENV_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        ENV_FILE.write_text("", encoding="utf-8")


def set_config(key: str, value: str) -> None:
    key = key.strip().upper()
    if key not in ALLOWED_KEYS:
        raise ValueError(f"Unknown config key: {key}. Allowed: {sorted(ALLOWED_KEYS)}")
    ensure_env_file()
    set_key(str(ENV_FILE), key, value, quote_mode="never")
    os.environ[key] = value


def get_config(key: str) -> str | None:
    key = key.strip().upper()
    return os.environ.get(key)


def visible_config() -> dict[str, str]:
    out: dict[str, str] = {}
    for k in sorted(ALLOWED_KEYS):
        v = os.environ.get(k, "")
        if k in SECRET_KEYS and v:
            v = v[:4] + "…" + v[-2:] if len(v) > 6 else "***"
        out[k] = v
    return out
