from collections import Counter
from datetime import date

from .batch_processor import ProcessResult
from .notifier import NotificationPayload

_BAR_MAX = 10
_BAR_CHAR = "▓"
_TODAY = lambda: date.today().strftime("%b %d")  # noqa: E731


def _bar(count: int, max_count: int) -> str:
    if max_count == 0:
        return ""
    return _BAR_CHAR * round(count / max_count * _BAR_MAX)


def build_run_summary(
    results: list[ProcessResult],
    mode: str = "run",
    account: str = "",
    inbox_stats: dict | None = None,
) -> NotificationPayload:
    total = len(results)
    ok = [r for r in results if r.ok and r.classification]
    errors = sum(1 for r in results if r.error and not r.dropped)
    dropped = sum(1 for r in results if r.dropped)

    counts: Counter[str] = Counter(r.classification.category for r in ok)  # type: ignore[union-attr]
    max_count = max(counts.values(), default=1)

    mode_label = {
        "default": "Default run",
        "test": "Test (dry run)",
        "batch": "Batch run",
        "range": "Range run",
    }.get(mode, mode.title())

    subtitle = mode_label + (f" · {account}" if account else "")
    title = f"📬 EmailSorter — {_TODAY()}"

    lines: list[str] = [f"<i>{subtitle}</i>", "", f"<b>{total}</b> emails processed", ""]

    if counts:
        col_w = max(len(c) for c in counts) + 1
        rows = "\n".join(
            f"{cat:<{col_w}} {cnt:>3}  {_bar(cnt, max_count)}"
            for cat, cnt in counts.most_common()
        )
        lines += [f"<code>{rows}</code>", ""]

    if inbox_stats:
        lines.append(f"Inbox unread: {inbox_stats.get('inbox_unread', 0)}")

    footer: list[str] = []
    if errors:
        footer.append(f"Errors: {errors}")
    if dropped:
        footer.append(f"Dropped: {dropped}")
    lines.append(" · ".join(footer) if footer else "No errors")

    return NotificationPayload(title=title, body="\n".join(lines))


# Keep old name as alias so any existing callers don't break.
build_summary = build_run_summary


def build_inbox_summary(stats: dict) -> NotificationPayload:
    account = stats.get("account", "")
    provider = stats.get("provider", "")

    lines: list[str] = [
        f"<i>{account} ({provider})</i>",
        "",
        f"Inbox total:  <b>{stats.get('inbox_total', 0)}</b>",
        f"Inbox unread: <b>{stats.get('inbox_unread', 0)}</b>",
    ]

    if provider == "gmail":
        lines += [
            f"Threads:      {stats.get('inbox_threads', 0)}",
            f"Account msgs: {stats.get('account_total_messages', 0)}",
            f"User labels:  {len(stats.get('user_labels', []))}",
        ]
    elif provider == "outlook":
        lines.append(f"Sub-folders:  {stats.get('inbox_child_folders', 0)}")

    folders = stats.get("folders", [])
    if folders:
        lines.append("")
        col_w = max(len(f["name"]) for f in folders) + 1
        rows = "\n".join(
            f"{f['name']:<{col_w}} {f['total']:>5}"
            + (f"  ({f['unread']} unread)" if f["unread"] else "")
            for f in folders
        )
        lines.append(f"<code>{rows}</code>")

    return NotificationPayload(title=f"📊 Inbox Stats — {_TODAY()}", body="\n".join(lines))


def build_status_summary(data: dict, dropped_count: int) -> NotificationPayload:
    last = data.get("last_run") or {}
    batch = data.get("batch_state", {})

    lines: list[str] = []
    if last:
        lines += [
            f"Last run:  {last.get('timestamp', 'unknown')}",
            f"Account:   {last.get('account', '')}",
            f"Provider:  {last.get('provider', '')}",
            f"Mode:      {last.get('mode', '')}",
            f"Processed: <b>{last.get('emails_processed', 0)}</b> emails",
        ]
    else:
        lines.append("No runs recorded yet.")

    lines += [
        "",
        f"Batch active:    {batch.get('active', False)}",
        f"Batch processed: {len(batch.get('completed_ids', []))}",
        f"Dropped emails:  {dropped_count}",
    ]

    return NotificationPayload(title=f"📋 Status — {_TODAY()}", body="\n".join(lines))
