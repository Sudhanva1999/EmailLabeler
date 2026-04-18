"""Interactive Gmail label cleanup tool.

Lists user-created labels, lets you select which to delete, then removes them.

Usage:
    python cleanup_labels.py                    # show all user labels
    python cleanup_labels.py --prefix AutoSort  # filter by prefix
"""

import argparse
import sys

from rich.console import Console
from rich.table import Table

from src.config import ensure_env_file, load_env
from src.email_providers.gmail import GmailProvider

console = Console()


def parse_selection(raw: str, max_index: int) -> list[int] | None:
    """Parse user input like '1,3,5' or '1-5' or 'all' into 0-based indices."""
    raw = raw.strip().lower()
    if raw in ("q", "quit", ""):
        return None
    indices: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part == "all":
            return list(range(max_index))
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                indices.update(range(int(a) - 1, int(b)))
            except ValueError:
                console.print(f"[red]Bad range: {part!r}[/red]")
                return []
        else:
            try:
                indices.add(int(part) - 1)
            except ValueError:
                console.print(f"[red]Not a number: {part!r}[/red]")
                return []
    out = [i for i in sorted(indices) if 0 <= i < max_index]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactively delete Gmail labels.")
    parser.add_argument("--prefix", help="Only show labels starting with this prefix (e.g. AutoSort)")
    args = parser.parse_args()

    ensure_env_file()
    load_env()

    console.print("[bold]Authenticating Gmail…[/bold]")
    provider = GmailProvider()
    provider.authenticate()
    console.print(f"Connected as [cyan]{provider.account}[/cyan]\n")

    labels = provider.list_labels()
    if args.prefix:
        labels = [l for l in labels if l["name"].startswith(args.prefix)]

    if not labels:
        msg = f"No user labels" + (f" matching prefix '{args.prefix}'" if args.prefix else "")
        console.print(f"[yellow]{msg}.[/yellow]")
        return 0

    while True:
        table = Table(title=f"Gmail Labels ({len(labels)} total)", show_lines=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Label name")
        table.add_column("ID", style="dim")
        for i, label in enumerate(labels, 1):
            table.add_row(str(i), label["name"], label["id"])
        console.print(table)

        console.print(
            "\nEnter label numbers to delete "
            "[dim](e.g. 1,3,5 or 2-6 or all)[/dim] "
            "or [bold]q[/bold] to quit:"
        )
        try:
            raw = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Aborted.[/dim]")
            return 0

        selected = parse_selection(raw, len(labels))
        if selected is None:
            console.print("[dim]Quit.[/dim]")
            return 0
        if not selected:
            console.print("[yellow]No valid selection.[/yellow]\n")
            continue

        chosen = [labels[i] for i in selected]
        console.print(f"\nAbout to delete [bold red]{len(chosen)}[/bold red] label(s):")
        for l in chosen:
            console.print(f"  [red]✗[/red] {l['name']}")

        try:
            confirm = input("\nConfirm? [y/N] ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Aborted.[/dim]")
            return 0

        if confirm != "y":
            console.print("[dim]Cancelled.[/dim]\n")
            continue

        deleted_ids = {l["id"] for l in chosen}
        for label in chosen:
            try:
                provider.delete_label(label["id"])
                console.print(f"[green]✓[/green] Deleted: {label['name']}")
            except Exception as exc:
                console.print(f"[red]✗[/red] Failed to delete {label['name']}: {exc}")

        labels = [l for l in labels if l["id"] not in deleted_ids]
        console.print()

        if not labels:
            console.print("[green]No labels remaining.[/green]")
            return 0


if __name__ == "__main__":
    sys.exit(main())
