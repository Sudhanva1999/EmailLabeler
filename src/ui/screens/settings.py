from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from ...config import ALLOWED_KEYS, set_config, visible_config


class SettingsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]
    CSS = """
    #form { height: auto; padding: 1; }
    #form Input { width: 60; }
    DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("[bold]Configuration[/bold] — edits write to .env immediately", classes="title")
        yield DataTable(id="config_table")
        with Vertical(id="form"):
            yield Label("Set a config value:")
            with Horizontal():
                yield Input(placeholder=f"KEY (one of: {', '.join(sorted(ALLOWED_KEYS))[:60]}...)", id="key")
                yield Input(placeholder="value", id="value")
                yield Button("Save", id="save", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Key", "Value")
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for k, v in visible_config().items():
            table.add_row(k, v or "[dim](unset)[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            key_input = self.query_one("#key", Input)
            value_input = self.query_one("#value", Input)
            key = key_input.value.strip()
            value = value_input.value
            if not key:
                self.notify("Key required", severity="warning")
                return
            try:
                set_config(key, value)
                self.notify(f"Saved {key.upper()}", severity="information")
                key_input.value = ""
                value_input.value = ""
                self._refresh_table()
            except ValueError as exc:
                self.notify(str(exc), severity="error")
