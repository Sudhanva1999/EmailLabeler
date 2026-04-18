# EmailSorter

Auto-label your Gmail or Outlook inbox using an LLM. Categories are defined in `categories.json`. The LLM layer is plug-and-play — switch between Google Gemini (cloud) and any OpenAI-compatible local model (Ollama, LM Studio, etc.) by changing one env var.

## Features

- **Two email providers**: Gmail (OAuth) and Outlook / Microsoft Graph (device flow)
- **Two LLM backends**: Google Gemini (default) and local OpenAI-compatible servers
- **Three run modes**: default (since last run), date range, resumable batch
- **Dry-run / test mode**: classify the latest N emails without modifying the account
- **Lightweight Textual TUI**: run jobs, watch live output, edit configuration
- **Persistent metadata**: tracks last-run timestamp, account, and batch progress for resume

---

## Prerequisites

- Python 3.11+
- A Google Cloud project with the Gmail API enabled (for Gmail), or an Azure App Registration (for Outlook)
- One of:
  - A Google Gemini API key, **or**
  - A locally running OpenAI-compatible LLM server (e.g. Ollama on `http://localhost:11434`)

---

## Installation

```bash
git clone <this-repo> EmailSorter
cd EmailSorter

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` (or use `python main.py config set KEY VALUE` — see below).

---

## Provider Setup

### Gmail

Connects via IMAP using a Google App Password — no OAuth flow, no browser popup, no redirect URIs.

1. Enable 2-Step Verification on your Google account (required for App Passwords)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create a new App Password → choose **Mail** → any device name
4. Copy the 16-character password (no spaces)

Required `.env` keys:
```ini
EMAIL_PROVIDER=gmail
EMAIL_ACCOUNT=you@gmail.com
GMAIL_API_KEY=abcdabcdabcdabcd   # 16-char app password, no spaces
```

### Outlook (Microsoft Graph)

1. Register an app in [Azure Portal → App registrations](https://portal.azure.com/) (Public client, redirect URI: `http://localhost`)
2. Under **API permissions**, add `Mail.ReadWrite` (delegated)
3. Copy the **Application (client) ID**

Required `.env` keys:
```ini
EMAIL_PROVIDER=outlook
OUTLOOK_CLIENT_ID=<your client id>
OUTLOOK_TENANT_ID=common        # or your tenant id
OUTLOOK_TOKEN_FILE=credentials/outlook_token.json
```

The first run uses device-flow auth — a code is printed to the terminal and you authorize in a browser.

### LLM — Gemini (cloud, default)

1. Get a key from [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Set:
```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=<your key>
GEMINI_MODEL=gemini-1.5-flash    # or gemini-1.5-pro
```

### LLM — Local (Ollama / LM Studio)

Start your local server (e.g. `ollama serve`, then `ollama pull llama3`).

```ini
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_MODEL=llama3
LOCAL_LLM_API_KEY=ollama         # any non-empty string for Ollama
```

Switching providers later requires only changing `LLM_PROVIDER` (no code changes).

---

## Configuration Reference

All config lives in `.env`. The full list of keys (also visible via `python main.py config show`):

| Key | Description |
|---|---|
| `EMAIL_PROVIDER` | `gmail` or `outlook` |
| `EMAIL_ACCOUNT` | Your Gmail or Outlook email address |
| `GMAIL_API_KEY` | Google App Password (16 chars, from myaccount.google.com/apppasswords) |
| `OUTLOOK_CLIENT_ID` | Azure App Registration client id |
| `OUTLOOK_TENANT_ID` | Azure tenant id (`common` for personal accounts) |
| `OUTLOOK_CLIENT_SECRET` | Optional — only needed for confidential client flow |
| `OUTLOOK_TOKEN_FILE` | Where the cached Outlook token is stored |
| `LLM_PROVIDER` | `gemini` or `local` |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GEMINI_MODEL` | Default `gemini-1.5-flash` |
| `LOCAL_LLM_BASE_URL` | OpenAI-compatible base URL |
| `LOCAL_LLM_MODEL` | Local model name |
| `LOCAL_LLM_API_KEY` | API key (use `ollama` for Ollama) |
| `BATCH_SIZE` | Emails processed per batch chunk before checkpointing (default 10) |
| `LABEL_PREFIX` | Prefix used for created labels, default `AutoSort` (e.g. `AutoSort/finance`, `AutoSort/tag/important`) |
| `MAX_CLASSIFY_RETRIES` | Max attempts before an email is dropped (default 5) |
| `BODY_CHAR_LIMIT` | Max characters of normalized body sent to the LLM (default 4000) |
| `DROPPED_LOG_FILE` | JSONL file where dropped emails are recorded (default `dropped_emails.jsonl`) |

---

## Commands

The CLI entry point is `python main.py`. Running it with no arguments launches the TUI.

### `run` — process emails

```bash
python main.py run [OPTIONS]
```

| Option | Description |
|---|---|
| (no flags) | **Default mode** — fetch all emails received after the last successful run |
| `--from YYYY-MM-DD` | Start of date range |
| `--to YYYY-MM-DD` | End of date range |
| `--batch` | **Batch mode** — fetch from the beginning, process in chunks of `BATCH_SIZE`, resumable |
| `--fresh` | With `--batch`: ignore previous batch progress and start over |
| `--max-batches N` | With `--batch`: stop after N chunks (useful to chip away at large inboxes) |
| `--test` | **Dry run** — classify the latest N emails and print results, but do **not** apply any labels or update metadata |
| `--limit N` | With `--test`: how many recent emails to fetch (default 10) |

Examples:

```bash
# Default: catch up since the last run
python main.py run

# Just the last week
python main.py run --from 2026-04-10 --to 2026-04-17

# Dry run on the 20 newest emails — see classifications without touching the inbox
python main.py run --test --limit 20

# Start a fresh batch run from the very beginning
python main.py run --batch --fresh

# Continue a previously interrupted batch
python main.py run --batch

# Process only the next 5 batches (50 emails) of an in-progress batch run
python main.py run --batch --max-batches 5
```

### `status` — show last run + batch state

```bash
python main.py status
```

Prints last-run timestamp, account, provider, mode, email count, and how many emails the active batch has processed.

### `config` — view/edit `.env`

```bash
python main.py config show
python main.py config set <KEY> <VALUE>
```

Examples:
```bash
python main.py config set LLM_PROVIDER local
python main.py config set GEMINI_API_KEY sk-xxx...
python main.py config set BATCH_SIZE 25
python main.py config set EMAIL_PROVIDER outlook
```

Secret values (`GEMINI_API_KEY`, `OUTLOOK_CLIENT_SECRET`, `LOCAL_LLM_API_KEY`) are masked in `config show`.

### `compare.py` — A/B compare Gemini vs. local LLM

A standalone dry-run script that classifies the same emails with **both** LLM backends so you can pick the one that fits your inbox best. No labels are applied and `metadata.json` is not touched.

```bash
python compare.py                       # latest 10 emails, both providers
python compare.py --limit 25            # latest 25 emails
python compare.py --no-local            # only Gemini
python compare.py --no-gemini           # only local
python compare.py --output out/cmp.md --json out/cmp.json
```

By default the script writes timestamped reports next to the project root: `comparison_YYYYMMDD_HHMMSS.md` and `comparison_YYYYMMDD_HHMMSS.json`. The markdown file shows each email with sender / subject / snippet plus a small table comparing category, tags, confidence, and any per-provider error. The JSON file has the same data in a structured form for further analysis. A category-agreement summary is printed at the end (`gemini vs local: 7/10`).

Both providers must be configured in `.env` for a full comparison — otherwise pass `--no-gemini` / `--no-local` to skip the missing one.

### `ui` — launch the Textual TUI

```bash
python main.py ui
# or just:
python main.py
```

Inside the TUI:
- **Test (Dry Run)** — classify latest N emails (set N in the `Test limit` input)
- **Run Default** — same as `run` with no flags
- **Run Batch** — resumable batch from beginning
- **Run Range** — uses the `From`/`To` inputs
- **Reset Batch** — clears batch progress (next batch run starts fresh)
- `s` — open Settings (edit `.env` keys live)
- `q` — quit

---

## Reliability — Validation, Retries, and Dropped Emails

Every classification goes through three layers before a label is applied:

1. **Normalization** (`src/normalizer.py`) — before the email reaches the LLM, the body is stripped of `<script>`/`<style>` blocks, HTML tags, comments, URLs (replaced with `[link]`), long base64 blobs (replaced with `[blob]`), zero-width characters, and quoted reply chains (lines starting with `>` and long `On … wrote:`-style headers). Whitespace is collapsed, the body is truncated to `BODY_CHAR_LIMIT` characters at a word boundary, and HTML entities are decoded. The same passes (minus the body-only ones) clean the subject. This dramatically reduces token load — important for local LLMs.

2. **Strict validation** — the LLM's response must parse as JSON **and** the `category` must be exactly one of the keys in `categories.json`. Anything else (typo, made-up category, wrapped in markdown fences, missing field, malformed JSON, network blip) raises `ClassificationError`.

3. **Retry with stronger prompt** — on each retry the prompt is amended with a `=== RETRY ===` section that re-states the allowed category list and re-emphasises "JSON only". Up to `MAX_CLASSIFY_RETRIES` attempts (default 5) with a small linear backoff. If every attempt fails, the email is **dropped**:
   - It is **not** labelled
   - It is **not** added to the batch's `completed_ids` (so it will be retried automatically on the next batch run)
   - A line is appended to `dropped_emails.jsonl` (path configurable via `DROPPED_LOG_FILE`):
     ```json
     {"timestamp": "...", "id": "...", "subject": "...", "sender": "...", "date": "...", "attempts": 5, "error": "Invalid category 'shopping'; must be one of [...]"}
     ```

`python main.py status` shows the current dropped count.

## How Labels Are Applied

For an email classified as `category=finance, tags=[important, financial]`, with `LABEL_PREFIX=AutoSort`:

- **Gmail**: applies labels `AutoSort/finance`, `AutoSort/tag/important`, `AutoSort/tag/financial` (created on demand)
- **Outlook**: sets the `categories` field to the same names

The category and tag lists are read verbatim from `categories.json` — edit that file to change the taxonomy.

---

## Metadata File (`metadata.json`)

Auto-created and maintained at the project root.

```json
{
  "last_run": {
    "timestamp": "2026-04-17T10:30:00+00:00",
    "provider": "gmail",
    "account": "you@gmail.com",
    "emails_processed": 42,
    "mode": "default",
    "date_range": { "from": "...", "to": "..." }
  },
  "batch_state": {
    "active": false,
    "started_at": "...",
    "completed_ids": ["msg_id_1", "msg_id_2", "..."],
    "last_processed_date": "...",
    "provider": "gmail",
    "account": "you@gmail.com"
  },
  "history": [ /* last 50 runs */ ]
}
```

- **Default mode** uses `last_run.timestamp` as the lower bound
- **Batch mode** skips any email id present in `batch_state.completed_ids`, so interrupted runs (Ctrl+C, network error) resume from where they stopped on the next `--batch` invocation

---

## Project Layout

```
EmailSorter/
├── .env                    # secrets and config (gitignored)
├── .env.example            # template
├── categories.json         # source of truth for categories + tags
├── metadata.json           # auto-managed run history (gitignored)
├── credentials/            # OAuth tokens (gitignored)
├── main.py                 # CLI entry point
└── src/
    ├── llm/                # base ABC + Gemini + Local providers
    ├── email_providers/    # base ABC + Gmail + Outlook adapters
    ├── categorizer.py      # builds prompt from categories.json
    ├── metadata.py         # metadata.json read/write
    ├── batch_processor.py  # default/range/batch/test orchestration
    ├── config.py           # .env read/write helpers
    └── ui/                 # Textual TUI
```

---

## Troubleshooting

**"GEMINI_API_KEY is not set"** — run `python main.py config set GEMINI_API_KEY <key>` or edit `.env` directly.

**"Gmail credentials file not found"** — download the OAuth client JSON from Google Cloud Console and place it at the path shown in the error (default `credentials/gmail_credentials.json`).

**Browser doesn't open during Gmail auth** — the auth flow uses a local server on a random port; ensure your terminal can spawn a browser (or copy the URL printed in the terminal manually).

**Outlook auth: "AADSTS65001"** — admin consent required for `Mail.ReadWrite`. For personal Microsoft accounts, set `OUTLOOK_TENANT_ID=consumers`. For work/school accounts, ask an admin to grant consent.

**Local LLM returns invalid JSON** — make sure your model supports `response_format: json_object` (most modern Llama-3 / Mistral instruct models do). Otherwise edit `src/llm/local.py` to drop that field and rely on the system prompt.

**Reset everything** — delete `metadata.json` and the files under `credentials/`. Next run will re-auth and start from scratch.

---

## Notes

- Email body content is truncated to 4000 characters before being sent to the LLM (configurable in `Categorizer(body_char_limit=...)`).
- Only `last_run.history` keeps the last 50 entries — older history is dropped.
- Gmail's `messages.list` query uses Unix timestamps for date filters; sub-day precision is best-effort (the API rounds).
