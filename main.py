import argparse
import os
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from src.batch_processor import BatchProcessor, ProcessResult
from src.categorizer import Categorizer
from src.config import (
    CATEGORIES_FILE,
    KEYWORD_ROUTES_FILE,
    ensure_env_file,
    load_env,
    set_config,
    visible_config,
)
from src.dropped_log import default_dropped_log
from src.email_providers import get_email_provider
from src.keyword_router import KeywordRouter, Route, RouteValidationError
from src.llm import get_llm_provider
from src.metadata import Metadata
from src.notifier import NotificationPayload, get_notifier
from src.reviewer import PostRunReviewer
from src.summarizer import build_inbox_summary, build_run_summary, build_status_summary

console = Console()


def _parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _routes_path() -> str:
    return os.getenv("KEYWORD_ROUTES_FILE") or str(KEYWORD_ROUTES_FILE)


def _load_router() -> KeywordRouter:
    import json

    with open(CATEGORIES_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return KeywordRouter(
        _routes_path(),
        valid_categories=list(data["primary_categories"].keys()),
        valid_tags=list(data["tags"].keys()),
    )


def _build_processor() -> tuple[BatchProcessor, Categorizer]:
    provider = get_email_provider()
    provider.authenticate()
    llm = get_llm_provider()
    router = _load_router()
    categorizer = Categorizer(CATEGORIES_FILE, llm, router=router)
    metadata = Metadata()
    dropped = default_dropped_log()
    return BatchProcessor(provider, categorizer, metadata, dropped_log=dropped), categorizer


def _print_progress(result: ProcessResult) -> None:
    if result.ok and result.classification:
        c = result.classification
        tags = f" [{', '.join(c.tags)}]" if c.tags else ""
        console.print(
            f"[green]✓[/green] {result.email.sender[:40]:<40} "
            f"→ [cyan]{c.category}[/cyan]{tags}  "
            f"[dim]({result.email.subject[:60]})[/dim]"
        )
    elif result.dropped:
        console.print(
            f"[yellow]⊘ DROPPED[/yellow] {result.email.id} "
            f"[dim]{result.email.subject[:60]}[/dim] — {result.error}"
        )
    else:
        console.print(
            f"[red]✗[/red] {result.email.id} "
            f"[dim]{result.email.subject[:60]}[/dim] — {result.error}"
        )


def _notify(payload: NotificationPayload) -> None:
    notifier = get_notifier()
    if notifier is None:
        return
    try:
        notifier.send(payload)
        console.print("[dim]Telegram notification sent.[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Notification failed:[/yellow] {exc}")


def _notify_run(results: list[ProcessResult], mode: str, proc: BatchProcessor, is_test: bool) -> None:
    inbox_stats = None
    if not is_test:
        try:
            inbox_stats = proc.provider.get_inbox_stats()
        except Exception:
            pass
    _notify(build_run_summary(results, mode=mode, account=proc.provider.account, inbox_stats=inbox_stats))


def cmd_run(args: argparse.Namespace) -> int:
    proc, categorizer = _build_processor()
    if args.test:
        console.print(
            f"[bold yellow]Test mode (dry run)[/bold yellow] — "
            f"latest {args.limit} emails, no labels applied, no metadata changes."
        )
        results = proc.run_test(limit=args.limit, on_progress=_print_progress)
        mode = "test"
    elif args.batch:
        console.print(f"[bold]Batch mode[/bold] (size={proc.batch_size}, fresh={args.fresh})")
        results = proc.run_batch(on_progress=_print_progress, fresh=args.fresh, max_batches=args.max_batches)
        mode = "batch"
    elif args.from_date or args.to_date:
        date_from = _parse_date(args.from_date) if args.from_date else _parse_date("1970-01-01")
        date_to = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
        console.print(f"[bold]Range mode[/bold] {date_from.date()} → {date_to.date()}")
        results = proc.run_range(date_from, date_to, on_progress=_print_progress)
        mode = "range"
    else:
        console.print("[bold]Default mode[/bold] (since last run)")
        results = proc.run_default(on_progress=_print_progress)
        mode = "default"
    console.print(f"[bold green]Done.[/bold green] Processed {len(results)} emails.")
    _notify_run(results, mode, proc, is_test=args.test)

    if args.review:
        reviewer = PostRunReviewer(
            results=results,
            provider=proc.provider,
            categorizer=categorizer,
            console=console,
            apply_enabled=not args.test,
        )
        reviewer.run()
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    metadata = Metadata()
    last = metadata.data.get("last_run")
    batch = metadata.data.get("batch_state", {})
    table = Table(title="EmailSorter Status")
    table.add_column("Field"); table.add_column("Value")
    if last:
        table.add_row("Last run", last.get("timestamp", ""))
        table.add_row("Account", last.get("account", ""))
        table.add_row("Provider", last.get("provider", ""))
        table.add_row("Mode", last.get("mode", ""))
        table.add_row("Emails processed", str(last.get("emails_processed", 0)))
    else:
        table.add_row("Last run", "(never)")
    table.add_row("Batch active", str(batch.get("active", False)))
    table.add_row("Batch processed", str(len(batch.get("completed_ids", []))))
    table.add_row("Batch last date", str(batch.get("last_processed_date") or ""))
    dropped = default_dropped_log()
    table.add_row("Dropped emails", f"{dropped.count()} ({dropped.path})")
    console.print(table)
    _notify(build_status_summary(metadata.data, dropped.count()))
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    from src.config import SECRET_KEYS
    set_config(args.key, args.value)
    console.print(f"[green]Set[/green] {args.key.upper()} in .env")
    display_val = "***" if args.key.upper() in SECRET_KEYS else args.value
    _notify(NotificationPayload(
        title="⚙️ Config Updated",
        body=f"<code>{args.key.upper()} = {display_val}</code>",
    ))
    return 0


def cmd_config_show(_: argparse.Namespace) -> int:
    table = Table(title="Configuration (.env)")
    table.add_column("Key"); table.add_column("Value")
    for k, v in visible_config().items():
        table.add_row(k, v)
    console.print(table)
    return 0


def cmd_notify_test(_: argparse.Namespace) -> int:
    notifier = get_notifier()
    if notifier is None:
        console.print(
            "[yellow]NOTIFY_PROVIDER is not set to 'telegram'. "
            "Run: python main.py config set NOTIFY_PROVIDER telegram[/yellow]"
        )
        return 1
    try:
        notifier.send(NotificationPayload(
            title="📬 EmailSorter — Test",
            body="Connection verified ✓\n\nNotifications are working correctly.",
        ))
        console.print("[green]Test notification sent.[/green] Check your Telegram.")
        return 0
    except Exception as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        return 1


def cmd_inbox_stats(_: argparse.Namespace) -> int:
    provider = get_email_provider()
    provider.authenticate()
    stats = provider.get_inbox_stats()

    console.print(f"\n[bold]Inbox stats for[/bold] [cyan]{stats['account']}[/cyan] ([dim]{stats['provider']}[/dim])\n")

    summary = Table(title="Summary", show_header=True)
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")

    summary.add_row("Inbox total", str(stats["inbox_total"]))
    summary.add_row("Inbox unread", str(stats["inbox_unread"]))

    if stats["provider"] == "gmail":
        summary.add_row("Inbox threads", str(stats.get("inbox_threads", 0)))
        summary.add_row("Inbox threads unread", str(stats.get("inbox_threads_unread", 0)))
        summary.add_row("Account total messages", str(stats.get("account_total_messages", 0)))
        summary.add_row("Account total threads", str(stats.get("account_total_threads", 0)))
        summary.add_row("User labels", str(len(stats.get("user_labels", []))))
    elif stats["provider"] == "outlook":
        summary.add_row("Inbox sub-folders", str(stats.get("inbox_child_folders", 0)))

    console.print(summary)

    if stats["folders"]:
        folder_table = Table(title="Folders", show_header=True)
        folder_table.add_column("Folder")
        folder_table.add_column("Total", justify="right")
        folder_table.add_column("Unread", justify="right")
        for f in stats["folders"]:
            unread_str = f"[yellow]{f['unread']}[/yellow]" if f["unread"] else "0"
            folder_table.add_row(f["name"], str(f["total"]), unread_str)
        console.print(folder_table)

    if stats.get("user_labels"):
        label_table = Table(title="User Labels", show_header=True)
        label_table.add_column("Label")
        for label in sorted(stats["user_labels"]):
            label_table.add_row(label)
        console.print(label_table)

    _notify(build_inbox_summary(stats))
    return 0


def cmd_ui(_: argparse.Namespace) -> int:
    from src.ui.app import EmailSorterApp
    EmailSorterApp().run()
    return 0


def cmd_routes_list(_: argparse.Namespace) -> int:
    try:
        router = _load_router()
    except RouteValidationError as exc:
        console.print(f"[red]Invalid {_routes_path()}:[/red] {exc}")
        return 1
    routes = router.routes
    if not routes:
        console.print(f"[dim]No routes defined at {router.path}[/dim]")
        return 0
    table = Table(title=f"Keyword routes ({router.path})")
    for col in ("Name", "Fields", "Mode", "Keywords", "→ Category", "Tags"):
        table.add_column(col)
    for r in routes:
        table.add_row(
            r.name,
            ", ".join(r.fields),
            r.mode,
            ", ".join(r.keywords),
            r.category,
            ", ".join(r.tags) or "-",
        )
    console.print(table)
    return 0


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
    except EOFError:
        return default
    return val or default


def cmd_routes_add(_: argparse.Namespace) -> int:
    try:
        router = _load_router()
    except RouteValidationError as exc:
        console.print(f"[red]Existing routes file is invalid:[/red] {exc}")
        return 1

    cats = sorted(router._valid_categories)
    tags = sorted(router._valid_tags)

    name = _prompt("Rule name")
    if not name:
        console.print("[yellow]Cancelled (no name).[/yellow]")
        return 1
    fields_raw = _prompt("Fields to match (comma-separated: sender,subject,body,snippet)", "sender,subject")
    fields = [f.strip() for f in fields_raw.split(",") if f.strip()]
    keywords_raw = _prompt("Keywords (comma-separated)")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    if not keywords:
        console.print("[yellow]Cancelled (no keywords).[/yellow]")
        return 1
    mode = _prompt("Match mode (any/all)", "any")

    console.print(f"Categories: {', '.join(cats)}")
    category = _prompt("Category")
    console.print(f"Tags: {', '.join(tags)}")
    tags_raw = _prompt("Tags (comma-separated, empty for none)", "")
    route_tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    try:
        route = Route(
            name=name,
            fields=fields,
            keywords=keywords,
            mode=mode,
            category=category,
            tags=route_tags,
            confidence=1.0,
        )
        router.add(route)
    except (RouteValidationError, ValueError) as exc:
        console.print(f"[red]Failed: {exc}[/red]")
        return 1
    console.print(f"[green]Added[/green] route {name!r} → {category} {route_tags}")
    tags_str = f" [{', '.join(route_tags)}]" if route_tags else ""
    _notify(NotificationPayload(
        title="🔀 Route Added",
        body=f"<code>{name}</code> → {category}{tags_str}\nFields: {', '.join(fields)}\nKeywords: {', '.join(keywords)}\nMode: {mode}",
    ))
    return 0


def cmd_routes_remove(args: argparse.Namespace) -> int:
    try:
        router = _load_router()
    except RouteValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    if router.remove(args.name):
        console.print(f"[green]Removed[/green] {args.name!r}")
        _notify(NotificationPayload(title="🔀 Route Removed", body=f"<code>{args.name}</code> removed"))
        return 0
    console.print(f"[yellow]No route named {args.name!r}[/yellow]")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emailsorter", description="Auto-label your inbox with an LLM.")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Run the labeller")
    run_p.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD or ISO)")
    run_p.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD or ISO)")
    run_p.add_argument("--batch", action="store_true", help="Resumable batch from beginning")
    run_p.add_argument("--fresh", action="store_true", help="Reset batch progress")
    run_p.add_argument("--max-batches", type=int, default=None, help="Limit batches in this run")
    run_p.add_argument(
        "--test",
        action="store_true",
        help="Dry run: classify the latest N emails and print results, no labels applied",
    )
    run_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of latest emails to fetch in --test mode (default: 10)",
    )
    run_p.add_argument(
        "--review",
        "-R",
        action="store_true",
        help="After the run, review each labeled email and reassign labels interactively",
    )
    run_p.set_defaults(func=cmd_run)

    sub.add_parser("status", help="Show last run + batch state").set_defaults(func=cmd_status)

    cfg = sub.add_parser("config", help="View/edit config in .env")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd")
    cfg_set = cfg_sub.add_parser("set", help="Set a config key")
    cfg_set.add_argument("key"); cfg_set.add_argument("value")
    cfg_set.set_defaults(func=cmd_config_set)
    cfg_sub.add_parser("show", help="Show all config").set_defaults(func=cmd_config_show)

    routes = sub.add_parser("routes", help="Manage keyword routing rules")
    routes_sub = routes.add_subparsers(dest="routes_cmd")
    routes_sub.add_parser("list", help="List all rules").set_defaults(func=cmd_routes_list)
    routes_sub.add_parser("add", help="Add a rule interactively").set_defaults(func=cmd_routes_add)
    rm = routes_sub.add_parser("remove", help="Remove a rule by name")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_routes_remove)

    sub.add_parser("notify-test", help="Send a test Telegram notification to verify setup").set_defaults(func=cmd_notify_test)
    sub.add_parser("inbox-stats", help="Show inbox counts and folder breakdown").set_defaults(func=cmd_inbox_stats)
    sub.add_parser("ui", help="Launch the terminal UI").set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_env_file()
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_ui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
