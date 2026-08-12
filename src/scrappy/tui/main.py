"""Aplicacion Textual: arranque, navegacion y estado compartido.

La TUI no reimplementa nada. Construye un `ScrappyApp` —el mismo composition
root que usan la CLI y el bot— y las pantallas leen de ahi.

Dos cosas que se resuelven aqui y no en las pantallas:

- **El logging va a fichero.** Textual es dueno del terminal mientras corre, y
  un solo evento de structlog escrito en stdout pintaria basura encima de la
  interfaz. Ver `configure_logging(log_file=...)`.
- **Se degrada sin credenciales.** Si Telegram no esta configurado, la TUI
  arranca igualmente en modo solo lectura: se puede explorar y calibrar, pero
  el boton de publicar queda deshabilitado y la barra de estado dice por que.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static

from scrappy import __version__
from scrappy.app import ScrappyApp, load_settings_or_die
from scrappy.config.settings import Settings
from scrappy.observability.logging import configure_logging, get_logger
from scrappy.scheduler.jobs import PipelineScheduler
from scrappy.tui.screens.candidates import CandidatesScreen
from scrappy.tui.screens.dashboard import DashboardScreen
from scrappy.tui.screens.settings import SettingsScreen

log = get_logger(__name__)

#: Los logs de la sesion de TUI. No es un visor en vivo, pero deja el rastro
#: completo para cuando algo falla y hay que mirar.
TUI_LOG_FILE = Path("data/scrappy-tui.log")


class StatusBar(Static):
    """Linea inferior: estado general y ultimo aviso relevante."""

    message: reactive[str] = reactive("Iniciando…")

    def render(self) -> str:
        return self.message


class ScrappyTUI(App[None]):
    """Interfaz de terminal de Scrappy."""

    TITLE = "Scrappy"
    SUB_TITLE = f"curador de memes v{__version__}"
    CSS_PATH = "scrappy.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("d", "show_dashboard", "Panel"),
        Binding("c", "show_candidates", "Candidatos"),
        Binding("s", "show_settings", "Configuracion"),
        Binding("r", "refresh_data", "Refrescar"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self._settings = settings
        #: Se rellena en `on_mount`. Las pantallas deben comprobarlo: durante
        #: el primer instante y si el arranque falla, es None.
        self.scrappy: ScrappyApp | None = None
        self.scheduler: PipelineScheduler | None = None
        #: False cuando Telegram no esta configurado: sin publisher no se puede
        #: publicar, pero si explorar y calibrar.
        self.can_publish = False

    # ------------------------------------------------------------------
    # Composicion
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = "textual-dark"
        await self._boot()

    async def _boot(self) -> None:
        """Monta `ScrappyApp` y abre el panel."""
        try:
            settings = self._settings or load_settings_or_die()
        except Exception as exc:
            self.set_status(f"No se pudo leer la configuracion: {exc}")
            return

        # A fichero, no a stdout: Textual manda en el terminal.
        configure_logging(settings.log_level, "console", log_file=TUI_LOG_FILE)

        # Con publisher solo si hay credenciales; si no, modo solo lectura.
        self.can_publish = bool(
            settings.telegram_bot_token.get_secret_value() and settings.telegram_target_chat_id
        )

        try:
            self.scrappy = await ScrappyApp.create(settings, with_publisher=self.can_publish)
        except Exception as exc:
            log.exception("tui_boot_failed", error=str(exc))
            self.set_status(f"Error al arrancar: {exc}")
            return

        self.scheduler = PipelineScheduler(self.scrappy)

        await self.push_screen(DashboardScreen())
        if self.can_publish:
            self.set_status("Listo")
        else:
            self.set_status(
                "Modo solo lectura: falta configurar Telegram en .env, asi que no se puede publicar"
            )

    async def on_unmount(self) -> None:
        """Apagado ordenado: cierra el scheduler y purga los workspaces."""
        if self.scheduler is not None:
            self.scheduler.shutdown()
        if self.scrappy is not None:
            await self.scrappy.aclose()

    # ------------------------------------------------------------------
    # Utilidades para las pantallas
    # ------------------------------------------------------------------
    def set_status(self, message: str) -> None:
        try:
            self.query_one("#status-bar", StatusBar).message = message
        except Exception:
            log.debug("status_bar_missing", message=message)

    def require_scrappy(self) -> ScrappyApp | None:
        """Devuelve la aplicacion o avisa por pantalla de que no esta lista."""
        if self.scrappy is None:
            self.notify(
                "Scrappy todavia no ha arrancado; revisa la barra de estado.",
                severity="error",
            )
        return self.scrappy

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    async def _switch_to(self, screen: DashboardScreen | CandidatesScreen | SettingsScreen) -> None:
        """Sustituye la pantalla actual, evitando apilar duplicados."""
        if len(self.screen_stack) > 1:
            self.pop_screen()
        await self.push_screen(screen)

    async def action_show_dashboard(self) -> None:
        await self._switch_to(DashboardScreen())

    async def action_show_candidates(self) -> None:
        await self._switch_to(CandidatesScreen())

    async def action_show_settings(self) -> None:
        await self._switch_to(SettingsScreen())

    async def action_refresh_data(self) -> None:
        screen = self.screen
        refresh = getattr(screen, "refresh_data", None)
        if refresh is None:
            self.notify("Esta pantalla no tiene nada que refrescar.")
            return
        await refresh()


def run_tui(settings: Settings | None = None) -> None:
    """Punto de entrada. Lo llaman `scrappy tui` y `Scrappy.bat`."""
    ScrappyTUI(settings).run()
