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
from scrappy.diagnostics import run_diagnostics
from scrappy.observability.logging import configure_logging, get_logger
from scrappy.scheduler.jobs import PipelineScheduler
from scrappy.tui.screens.candidates import CandidatesScreen
from scrappy.tui.screens.dashboard import DashboardScreen
from scrappy.tui.screens.help import HelpScreen
from scrappy.tui.screens.settings import SettingsScreen
from scrappy.tui.screens.wizard import WizardResult, WizardScreen, hace_falta_asistente

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
        Binding("question_mark", "show_help", "Ayuda"),
        Binding("r", "refresh_data", "Refrescar"),
        # Mayuscula a proposito: recargar cierra conexiones y reconstruye la
        # aplicacion entera, y no debe quedar a un dedo de «refrescar».
        Binding("R", "recargar", "Recargar config"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        env_path: Path | None = None,
        show_wizard: bool = True,
    ) -> None:
        super().__init__()
        self._settings = settings
        #: El asistente de primera vez se puede desactivar: en los tests
        #: estorba, y a quien ya sabe lo que le falta no hay que insistirle.
        self._show_wizard = show_wizard
        #: Fichero `.env` que edita la pantalla de ajustes. Parametrizable para
        #: que los tests no toquen el del usuario.
        self.env_path = env_path or Path(".env")
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
            settings = self._settings or load_settings_or_die(self.env_path)
        except Exception as exc:
            self.set_status(f"No se pudo leer la configuracion: {exc}")
            return

        # A fichero, no a stdout: Textual manda en el terminal.
        configure_logging(settings.log_level, "console", log_file=TUI_LOG_FILE)

        if await self._montar(settings) is None:
            return

        await self.push_screen(DashboardScreen())
        if self.can_publish:
            self.set_status("Listo")
        else:
            self.set_status(
                "Modo solo lectura: falta configurar Telegram en .env, asi que no se puede publicar"
            )

        await self._ofrecer_asistente(settings)

    async def _montar(self, settings: Settings) -> PipelineScheduler | None:
        """Construye `ScrappyApp` y su scheduler, o None si falla.

        Lo comparten el arranque y la recarga: son la misma operacion, y
        tenerla escrita dos veces era garantia de que una se quedara atras.
        """
        # Con publisher solo si hay credenciales; si no, modo solo lectura.
        self.can_publish = bool(
            settings.telegram_bot_token.get_secret_value() and settings.telegram_target_chat_id
        )

        try:
            self.scrappy = await ScrappyApp.create(settings, with_publisher=self.can_publish)
        except Exception as exc:
            log.exception("tui_boot_failed", error=str(exc))
            self.set_status(f"Error al arrancar: {exc}")
            return None

        self.scheduler = PipelineScheduler(self.scrappy)
        return self.scheduler

    # ------------------------------------------------------------------
    # Recarga en caliente
    # ------------------------------------------------------------------
    async def recargar(self) -> bool:
        """Aplica los cambios del `.env` sin cerrar la ventana.

        Los ajustes se leen una sola vez, al construir `ScrappyApp`: se
        reparten por los adapters, el cliente HTTP y el backend de estado, y
        ninguno de ellos vuelve a mirarlos. Por eso guardar el `.env` no bastaba
        y habia que reiniciar el proceso.

        Aqui se hace lo mismo que hace un reinicio, pero sin perder la sesion:
        se cierra la aplicacion vieja -que purga los workspaces y cierra las
        conexiones- y se monta una nueva con los ajustes releidos.

        Si el scheduler estaba corriendo se vuelve a arrancar; si no, no. Un
        reinicio silencioso del scheduler seria peor que no recargar.
        """
        corriendo = self.scheduler is not None and self.scheduler.next_run_at is not None

        try:
            settings = load_settings_or_die(self.env_path)
        except Exception as exc:
            self.set_status(f"No se pudo releer la configuracion: {exc}")
            self.notify(str(exc), severity="error", timeout=10)
            return False

        # Se cierra ANTES de montar lo nuevo: dos ScrappyApp vivos a la vez
        # significan dos backends de estado sobre la misma base de datos.
        if self.scheduler is not None:
            self.scheduler.shutdown()
        if self.scrappy is not None:
            await self.scrappy.aclose()
        self.scrappy = None
        self.scheduler = None

        configure_logging(settings.log_level, "console", log_file=TUI_LOG_FILE)

        # A partir de aqui `self._settings` ya no manda: lo que vale es el
        # fichero. Si no se limpiara, la siguiente recarga volveria a los
        # ajustes de arranque.
        self._settings = None

        scheduler = await self._montar(settings)
        if scheduler is None:
            return False

        if corriendo:
            scheduler.start()

        await self.action_refresh_data(silencioso=True)
        log.info("tui_recargada", scheduler=corriendo, timezone=str(settings.tzinfo))
        return True

    async def _ofrecer_asistente(self, settings: Settings) -> None:
        """Abre el asistente solo si hay algo que impide publicar.

        Sin red: la comprobacion tiene que ser instantanea, y las erratas de
        formato —que son las que mas cuestan— se detectan sin llamar a nadie.
        """
        if not self._show_wizard:
            return

        diagnosis = await run_diagnostics(settings, use_network=False)
        if not hace_falta_asistente(diagnosis):
            return

        # Con callback y no con `push_screen_wait`, que exige estar dentro de
        # un worker: esto corre en el arranque, no en uno.
        self.push_screen(WizardScreen(diagnosis), self._tras_asistente)

    def _tras_asistente(self, eleccion: WizardResult) -> None:
        if eleccion == "settings":
            self.call_later(self._switch_to, SettingsScreen())

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

    async def action_show_help(self) -> None:
        await self.push_screen(HelpScreen())

    async def action_refresh_data(self, silencioso: bool = False) -> None:
        """Recarga lo que muestra la pantalla actual.

        `silencioso` lo usa la recarga en caliente: alli refrescar es un paso
        intermedio, y avisar de que «esta pantalla no tiene nada que refrescar»
        seria ruido sobre una operacion que si hizo algo.
        """
        screen = self.screen
        refresh = getattr(screen, "refresh_data", None)
        if refresh is None:
            if not silencioso:
                self.notify("Esta pantalla no tiene nada que refrescar.")
            return
        await refresh()

    async def action_recargar(self) -> None:
        if await self.recargar():
            self.set_status("Configuracion recargada")
            self.notify("Configuracion recargada. No hace falta reiniciar.")


def run_tui(settings: Settings | None = None) -> None:
    """Punto de entrada. Lo llaman `scrappy tui` y `Scrappy.bat`."""
    ScrappyTUI(settings).run()


__all__ = ["ScrappyTUI", "StatusBar", "run_tui"]
