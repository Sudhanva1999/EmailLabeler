import os
import threading
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

import json

from ...batch_processor import BatchProcessor, ProcessResult
from ...categorizer import Categorizer
from ...config import CATEGORIES_FILE, KEYWORD_ROUTES_FILE
from ...dropped_log import default_dropped_log
from ...email_providers import get_email_provider
from ...keyword_router import KeywordRouter, RouteValidationError
from ...llm import get_llm_provider
from ...metadata import Metadata


class HomeScreen(Screen):
    BINDINGS = [
        ("s", "open_settings", "Settings"),
        ("q", "quit", "Quit"),
    ]
    CSS = """
    #header_bar { height: auto; padding: 1; background: $boost; }
    #controls { height: auto; padding: 1; }
    #controls Button { margin-right: 1; }
    #range { height: auto; padding: 0 1; }
    #range Input { width: 18; }
    RichLog { height: 1fr; border: round $primary; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._header_text(), id="header_bar")
        with Horizontal(id="controls"):
            yield Button("Test (Dry Run)", id="run_test", variant="warning")
            yield Button("Run Default", id="run_default", variant="primary")
            yield Button("Run Batch", id="run_batch", variant="success")
            yield Button("Run Range", id="run_range")
            yield Button("Reset Batch", id="reset_batch")
        with Horizontal(id="range"):
            yield Label("From:")
            yield Input(placeholder="YYYY-MM-DD", id="from_date")
            yield Label("To:")
            yield Input(placeholder="YYYY-MM-DD", id="to_date")
            yield Label("Test limit:")
            yield Input(placeholder="10", id="test_limit", value="10")
        yield RichLog(highlight=True, markup=True, id="log")
        yield Footer()

    def _header_text(self) -> str:
        meta = Metadata()
        last = meta.data.get("last_run") or {}
        provider = os.getenv("EMAIL_PROVIDER", "?")
        llm = os.getenv("LLM_PROVIDER", "?")
        return (
            f"[bold]EmailSorter[/bold]   "
            f"Email: [cyan]{provider}[/cyan]   LLM: [magenta]{llm}[/magenta]   "
            f"Last run: [yellow]{last.get('timestamp', '(never)')}[/yellow]   "
            f"Last count: [green]{last.get('emails_processed', 0)}[/green]"
        )

    def action_open_settings(self) -> None:
        from .settings import SettingsScreen
        self.app.push_screen(SettingsScreen())

    def on_screen_resume(self) -> None:
        self.query_one("#header_bar", Static).update(self._header_text())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._running:
            self.notify("A run is already in progress", severity="warning")
            return
        log = self.query_one(RichLog)
        if event.button.id == "reset_batch":
            Metadata().reset_batch()
            log.write("[yellow]Batch progress reset.[/yellow]")
            return
        if event.button.id == "run_test":
            limit_raw = self.query_one("#test_limit", Input).value.strip() or "10"
            try:
                limit = max(1, int(limit_raw))
            except ValueError:
                self.notify("Test limit must be an integer", severity="error")
                return
            log.write(f"[yellow]Dry run — no labels will be applied (limit={limit}).[/yellow]")
            self._launch(lambda p, cb: p.run_test(limit=limit, on_progress=cb), label=f"test x{limit}")
        elif event.button.id == "run_default":
            self._launch(lambda p, cb: p.run_default(on_progress=cb), label="default")
        elif event.button.id == "run_batch":
            self._launch(lambda p, cb: p.run_batch(on_progress=cb), label="batch")
        elif event.button.id == "run_range":
            from_v = self.query_one("#from_date", Input).value.strip()
            to_v = self.query_one("#to_date", Input).value.strip()
            if not from_v or not to_v:
                self.notify("Provide both From and To dates", severity="warning")
                return
            try:
                date_from = datetime.fromisoformat(from_v).replace(tzinfo=timezone.utc)
                date_to = datetime.fromisoformat(to_v).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                self.notify(f"Bad date: {exc}", severity="error")
                return
            self._launch(
                lambda p, cb: p.run_range(date_from, date_to, on_progress=cb),
                label=f"range {from_v}→{to_v}",
            )

    def _launch(self, runner, label: str) -> None:
        log = self.query_one(RichLog)
        log.write(f"[bold]Starting {label}…[/bold]")
        self._running = True

        def progress(result: ProcessResult) -> None:
            self.app.call_from_thread(self._log_result, result)

        def worker() -> None:
            try:
                provider = get_email_provider()
                provider.authenticate()
                llm = get_llm_provider()
                with open(CATEGORIES_FILE, "r", encoding="utf-8") as fh:
                    cats_data = json.load(fh)
                routes_path = os.getenv("KEYWORD_ROUTES_FILE") or str(KEYWORD_ROUTES_FILE)
                try:
                    router = KeywordRouter(
                        routes_path,
                        valid_categories=list(cats_data["primary_categories"].keys()),
                        valid_tags=list(cats_data["tags"].keys()),
                    )
                except RouteValidationError as exc:
                    self.app.call_from_thread(
                        log.write, f"[yellow]Ignoring invalid routes file:[/yellow] {exc}"
                    )
                    router = None
                categorizer = Categorizer(CATEGORIES_FILE, llm, router=router)
                meta = Metadata()
                dropped = default_dropped_log()
                proc = BatchProcessor(provider, categorizer, meta, dropped_log=dropped)
                results = runner(proc, progress)
                self.app.call_from_thread(log.write, f"[bold green]Done.[/bold green] {len(results)} emails.")
                self.app.call_from_thread(
                    self.query_one("#header_bar", Static).update, self._header_text()
                )
            except Exception as exc:
                self.app.call_from_thread(log.write, f"[bold red]Error:[/bold red] {exc}")
            finally:
                self._running = False

        threading.Thread(target=worker, daemon=True).start()

    def _log_result(self, result: ProcessResult) -> None:
        log = self.query_one(RichLog)
        if result.ok and result.classification:
            c = result.classification
            tags = f" [{', '.join(c.tags)}]" if c.tags else ""
            log.write(
                f"[green]✓[/green] {result.email.sender[:40]} "
                f"→ [cyan]{c.category}[/cyan]{tags} "
                f"[dim]{result.email.subject[:50]}[/dim]"
            )
        elif result.dropped:
            log.write(
                f"[yellow]⊘ DROPPED[/yellow] {result.email.subject[:50]} — {result.error}"
            )
        else:
            log.write(f"[red]✗[/red] {result.email.subject[:50]} — {result.error}")
