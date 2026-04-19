from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .batch_processor import ProcessResult
from .categorizer import Categorizer
from .email_providers import EmailProvider
from .llm import Classification


@dataclass
class ReviewStats:
    total: int = 0
    reviewed: int = 0
    reassigned: int = 0
    skipped: int = 0


class PostRunReviewer:
    def __init__(
        self,
        results: list[ProcessResult],
        provider: EmailProvider,
        categorizer: Categorizer,
        console: Console | None = None,
        apply_enabled: bool = True,
    ) -> None:
        self._results = [r for r in results if r.ok and r.classification]
        self._provider = provider
        self._categorizer = categorizer
        self._console = console or Console()
        self._apply = apply_enabled

    def run(self) -> ReviewStats:
        stats = ReviewStats(total=len(self._results))
        if not self._results:
            self._console.print("[dim]Nothing to review — no successful classifications in this run.[/dim]")
            return stats

        self._console.rule("[bold cyan]Post-run review[/bold cyan]")
        self._console.print(
            f"[dim]{stats.total} emails to review. "
            "[Enter] keep · [r] reassign · [s] skip remaining · [q] quit[/dim]\n"
        )

        for idx, result in enumerate(self._results, start=1):
            stats.reviewed += 1
            self._render_email(idx, stats.total, result)
            choice = self._prompt("Action [Enter=keep / r=reassign / s=skip / q=quit]: ").strip().lower()
            if choice == "":
                continue
            if choice == "s":
                stats.skipped = stats.total - idx
                self._console.print("[dim]Skipping remaining.[/dim]")
                break
            if choice == "q":
                stats.skipped = stats.total - idx
                self._console.print("[dim]Review aborted.[/dim]")
                break
            if choice == "r":
                if self._reassign(result):
                    stats.reassigned += 1
                continue
            self._console.print(f"[yellow]Unknown choice {choice!r} — keeping as-is.[/yellow]")

        self._console.rule()
        self._console.print(
            f"[green]Review complete.[/green] "
            f"reviewed={stats.reviewed} reassigned={stats.reassigned} skipped={stats.skipped}"
        )
        return stats

    def _render_email(self, idx: int, total: int, result: ProcessResult) -> None:
        email = result.email
        c = result.classification
        assert c is not None

        header = Text()
        header.append(f"[{idx}/{total}] ", style="dim")
        header.append(email.subject or "(no subject)", style="bold white")

        body = Text()
        body.append("From:    ", style="dim")
        body.append(f"{email.sender}\n")
        body.append("Date:    ", style="dim")
        body.append(f"{email.date.isoformat()}\n")
        body.append("Preview: ", style="dim")
        body.append(self._snippet(email.snippet or email.body or ""))
        body.append("\n\n")
        body.append("Category: ", style="dim")
        body.append(c.category, style="cyan bold")
        body.append("\n")
        body.append("Tags:     ", style="dim")
        body.append(", ".join(c.tags) if c.tags else "(none)", style="yellow")

        self._console.print(Panel(body, title=header, border_style="cyan"))

    @staticmethod
    def _snippet(text: str, limit: int = 200) -> str:
        flat = " ".join((text or "").split())
        return flat[:limit] + ("…" if len(flat) > limit else "")

    def _reassign(self, result: ProcessResult) -> bool:
        assert result.classification is not None
        old_cat = result.classification.category
        old_tags = list(result.classification.tags)

        new_cat = self._pick_category(old_cat)
        if new_cat is None:
            return False
        new_tags = self._pick_tags(old_tags)
        if new_tags is None:
            return False

        if new_cat == old_cat and set(new_tags) == set(old_tags):
            self._console.print("[dim]No change.[/dim]")
            return False

        if self._apply:
            try:
                self._provider.replace_labels(
                    result.email.id, old_cat, old_tags, new_cat, new_tags
                )
            except Exception as exc:
                self._console.print(f"[red]Failed to apply new labels: {exc}[/red]")
                return False

        result.classification = Classification(
            category=new_cat,
            tags=new_tags,
            confidence=1.0,
            raw=f"manual_review:{old_cat}->{new_cat}",
        )
        self._console.print(
            f"[green]Reassigned[/green] → [cyan]{new_cat}[/cyan] "
            f"tags=[{', '.join(new_tags) or 'none'}]"
        )
        return True

    def _pick_category(self, current: str) -> str | None:
        names = self._categorizer.category_names
        table = Table(title="Categories", show_header=False, box=None, padding=(0, 1))
        for i, name in enumerate(names, start=1):
            mark = " *" if name == current else "  "
            table.add_row(f"{i:>2}.", name, mark)
        self._console.print(table)
        raw = self._prompt("Pick category [number, Enter=keep current, q=cancel]: ").strip().lower()
        if raw == "":
            return current
        if raw == "q":
            return None
        if not raw.isdigit() or not (1 <= int(raw) <= len(names)):
            self._console.print("[yellow]Invalid number. Cancelled.[/yellow]")
            return None
        return names[int(raw) - 1]

    def _pick_tags(self, current: list[str]) -> list[str] | None:
        names = self._categorizer.tag_names
        table = Table(title="Tags (comma-separated numbers, or 'none')", show_header=False, box=None, padding=(0, 1))
        for i, name in enumerate(names, start=1):
            mark = " *" if name in current else "  "
            table.add_row(f"{i:>2}.", name, mark)
        self._console.print(table)
        raw = self._prompt("Pick tags [e.g. 1,3 · Enter=keep · none=clear · q=cancel]: ").strip().lower()
        if raw == "":
            return current
        if raw == "q":
            return None
        if raw == "none":
            return []
        try:
            picks = [int(p.strip()) for p in raw.split(",") if p.strip()]
        except ValueError:
            self._console.print("[yellow]Invalid selection. Cancelled.[/yellow]")
            return None
        if any(not (1 <= p <= len(names)) for p in picks):
            self._console.print("[yellow]Number out of range. Cancelled.[/yellow]")
            return None
        seen: set[str] = set()
        out: list[str] = []
        for p in picks:
            t = names[p - 1]
            if t not in seen:
                out.append(t)
                seen.add(t)
        return out

    def _prompt(self, message: str) -> str:
        try:
            return input(message)
        except EOFError:
            return "q"
