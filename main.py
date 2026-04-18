import argparse
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from src.batch_processor import BatchProcessor, ProcessResult
from src.categorizer import Categorizer
from src.config import (
    CATEGORIES_FILE,
    METADATA_FILE,
    ensure_env_file,
    load_env,
    set_config,
    visible_config,
)
from src.dropped_log import default_dropped_log
from src.email_providers import get_email_provider
from src.llm import get_llm_provider
from src.metadata import Metadata

console = Console()


def _parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_processor() -> BatchProcessor:
    provider = get_email_provider()
    provider.authenticate()
    llm = get_llm_provider()
    categorizer = Categorizer(CATEGORIES_FILE, llm)
    metadata = Metadata(METADATA_FILE)
    dropped = default_dropped_log()
    return BatchProcessor(provider, categorizer, metadata, dropped_log=dropped)


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


def cmd_run(args: argparse.Namespace) -> int:
    proc = _build_processor()
    if args.test:
        console.print(
            f"[bold yellow]Test mode (dry run)[/bold yellow] — "
            f"latest {args.limit} emails, no labels applied, no metadata changes."
        )
        n = proc.run_test(limit=args.limit, on_progress=_print_progress)
    elif args.batch:
        console.print(f"[bold]Batch mode[/bold] (size={proc.batch_size}, fresh={args.fresh})")
        n = proc.run_batch(on_progress=_print_progress, fresh=args.fresh, max_batches=args.max_batches)
    elif args.from_date or args.to_date:
        date_from = _parse_date(args.from_date) if args.from_date else _parse_date("1970-01-01")
        date_to = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
        console.print(f"[bold]Range mode[/bold] {date_from.date()} → {date_to.date()}")
        n = proc.run_range(date_from, date_to, on_progress=_print_progress)
    else:
        console.print("[bold]Default mode[/bold] (since last run)")
        n = proc.run_default(on_progress=_print_progress)
    console.print(f"[bold green]Done.[/bold green] Processed {n} emails.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    metadata = Metadata(METADATA_FILE)
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
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    set_config(args.key, args.value)
    console.print(f"[green]Set[/green] {args.key.upper()} in .env")
    return 0


def cmd_config_show(_: argparse.Namespace) -> int:
    table = Table(title="Configuration (.env)")
    table.add_column("Key"); table.add_column("Value")
    for k, v in visible_config().items():
        table.add_row(k, v)
    console.print(table)
    return 0


def cmd_ui(_: argparse.Namespace) -> int:
    from src.ui.app import EmailSorterApp
    EmailSorterApp().run()
    return 0


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
    run_p.set_defaults(func=cmd_run)

    sub.add_parser("status", help="Show last run + batch state").set_defaults(func=cmd_status)

    cfg = sub.add_parser("config", help="View/edit config in .env")
    cfg_sub = cfg.add_subparsers(dest="cfg_cmd")
    cfg_set = cfg_sub.add_parser("set", help="Set a config key")
    cfg_set.add_argument("key"); cfg_set.add_argument("value")
    cfg_set.set_defaults(func=cmd_config_set)
    cfg_sub.add_parser("show", help="Show all config").set_defaults(func=cmd_config_show)

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
