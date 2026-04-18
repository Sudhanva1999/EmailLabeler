from textual.app import App

from ..config import ensure_env_file, load_env
from .screens.home import HomeScreen


class EmailSorterApp(App):
    TITLE = "EmailSorter"
    SUB_TITLE = "Auto-label your inbox"

    def on_mount(self) -> None:
        ensure_env_file()
        load_env()
        self.push_screen(HomeScreen())


if __name__ == "__main__":
    EmailSorterApp().run()
