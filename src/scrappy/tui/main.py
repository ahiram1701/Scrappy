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
from scrappy.autostart import Autoarranque
from scrappy.bot.avisos import avisar_arranque
from scrappy.bot.listener import BotListener
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
        autoarranque: Autoarranque | None = None,
        escuchar: bool = False,
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
        #: Tarea de inicio de sesion. Inyectable para que los tests no toquen
        #: las tareas reales del sistema de nadie.
        self.autoarranque = autoarranque or Autoarranque()
        #: Escucha los comandos y los botones de Telegram. None cuando no hay
        #: credenciales, o cuando otro proceso ya esta escuchando.
        self.listener: BotListener | None = None
        #: Escuchar sale a la red y, peor, consume las actualizaciones del bot:
        #: dos procesos con el mismo token se las quitan el uno al otro. Por eso
        #: hay que pedirlo, y solo lo pide `run_tui`. Un test que lo activara
        #: sin querer le robaria las pulsaciones al bot de verdad.
        self._escuchar = escuchar

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

        self._arrancar_si_toca()
        await self._escuchar_telegram()
        await self.push_screen(DashboardScreen())
        if self.can_publish:
            self.set_status("Listo")
        else:
            self.set_status(
                "Modo solo lectura: falta configurar Telegram en .env, asi que no se puede publicar"
            )

        await self._ofrecer_asistente(settings)

    async def _montar(self, settings: Settings) -> tuple[ScrappyApp, PipelineScheduler] | None:
        """Construye `ScrappyApp` y su scheduler, o None si falla.

        Lo comparten el arranque y la recarga: son la misma operacion, y
        tenerla escrita dos veces era garantia de que una se quedara atras.

        Devuelve las dos piezas en vez de dejarlas solo en los atributos para
        que quien llama pueda usarlas sin volver a comprobar si son None.
        """
        # Con publisher solo si hay credenciales; si no, modo solo lectura.
        self.can_publish = bool(
            settings.telegram_bot_token.get_secret_value() and settings.telegram_target_chat_id
        )

        try:
            scrappy = await ScrappyApp.create(
                settings, with_publisher=self.can_publish, env_path=self.env_path
            )
        except Exception as exc:
            log.exception("tui_boot_failed", error=str(exc))
            self.set_status(f"Error al arrancar: {exc}")
            return None

        self.scrappy = scrappy
        self.scheduler = PipelineScheduler(scrappy)
        return scrappy, self.scheduler

    async def _escuchar_telegram(self) -> None:
        """Atiende los comandos y los botones mientras la TUI este abierta.

        Sin esto, la TUI publicaba y no oia: los botones que van bajo cada
        meme no tenian a quien preguntar, asi que pulsarlos no hacia nada.
        Publicar y escuchar son dos cosas distintas y solo `scrappy run`
        montaba la segunda.
        """
        if self.scrappy is None or not self.can_publish or not self._escuchar:
            return

        self.listener = BotListener(
            self.scrappy,
            self.scheduler,
            on_conflict=self._aviso_conflicto,
            recargador=self._recarga_pedida_por_telegram,
        )
        if await self.listener.start():
            log.info("tui_escuchando_telegram")
        else:
            self.listener = None

    def _recarga_pedida_por_telegram(self, motivo: str) -> bool:
        """Alguien cambio un ajuste desde el movil con la TUI abierta.

        Se encola en vez de recargar aqui mismo, y no es un detalle de estilo:
        esto lo llama un handler del bot, y recargar para el listener que lo
        esta ejecutando. Awaitarlo seria esperarse a si mismo.

        La recarga es la de siempre -la del boton «Recargar» del panel-, asi
        que la pantalla se sincroniza sola y no hay una segunda forma de
        recargar que mantener al dia.
        """
        self.call_later(self._recargar_y_avisar, motivo)
        return True

    async def _recargar_y_avisar(self, motivo: str) -> None:
        """Recarga y lo cuenta por los dos sitios: la pantalla y Telegram."""
        self.set_status(f"Recargando: {motivo}…")
        if not await self.recargar():
            return

        self.set_status(f"Recargado: {motivo}")
        if self.scrappy is not None:
            await avisar_arranque(self.scrappy, self.scheduler, motivo=motivo)

    def _aviso_conflicto(self, motivo: str) -> None:
        """Otro Scrappy esta escuchando con el mismo token.

        Lo llama el updater desde su propio bucle, asi que se encola en vez de
        tocar los widgets al vuelo.
        """
        self.call_later(self.set_status, motivo)

    def _arrancar_si_toca(self) -> None:
        """Arranca el scheduler si la configuracion dice que si.

        `scrappy run` lo arranca desde siempre, y la TUI no lo hacia: con
        `SCRAPPY_SCHEDULE_ENABLED=true` la pantalla de configuracion decia
        «activo» y el panel decia «parado», que era cierto pero se referia a
        otra cosa. Dos interfaces del mismo proyecto no pueden entender lo
        mismo de formas distintas.

        Sin Telegram configurado no se arranca: sin publisher, cada ronda
        seria un fallo cada N horas y ninguna publicacion.
        """
        if self.scheduler is None or not self.can_publish:
            return
        self.scheduler.start()

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

        El scheduler se vuelve a dejar como estaba, incluida la pausa: `paused`
        vive en `ScrappyApp`, que aqui se tira entera, asi que sin guardarlo
        antes un `/pause` se deshacia solo al guardar cualquier ajuste.
        """
        pausado = self.scrappy is not None and self.scrappy.paused

        try:
            settings = load_settings_or_die(self.env_path)
        except Exception as exc:
            self.set_status(f"No se pudo releer la configuracion: {exc}")
            self.notify(str(exc), severity="error", timeout=10)
            return False

        # Se cierra ANTES de montar lo nuevo: dos ScrappyApp vivos a la vez
        # significan dos backends de estado sobre la misma base de datos, y
        # dos escuchas sobre el mismo token significan que Telegram le da las
        # actualizaciones a uno de los dos y 409 al otro.
        if self.listener is not None:
            await self.listener.stop()
            self.listener = None
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

        montado = await self._montar(settings)
        if montado is None:
            return False
        scrappy, scheduler = montado

        scrappy.paused = pausado

        # La misma regla que al arrancar, y por el mismo motivo: si se acaba de
        # activar el scheduler en la configuracion, recargar tiene que aplicarlo.
        self._arrancar_si_toca()
        await self._escuchar_telegram()

        self._sincronizar_pantalla()
        log.info(
            "tui_recargada",
            scheduler=scheduler.running,
            pausado=pausado,
            timezone=str(settings.tzinfo),
        )
        return True

    def _sincronizar_pantalla(self) -> None:
        """Pone al dia la pantalla actual tras recargar.

        Se pide `sincronizar()` y no `refresh_data()` a proposito: en
        Candidatos, refrescar dispara el pipeline entero contra la red, y
        recargar la configuracion no es lo mismo que querer buscar contenido.
        """
        sincronizar = getattr(self.screen, "sincronizar", None)
        if sincronizar is not None:
            sincronizar()

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
        """Apagado ordenado: deja de escuchar, para el scheduler y purga."""
        if self.listener is not None:
            await self.listener.stop()
            self.listener = None
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
    """Punto de entrada. Lo llaman `scrappy tui` y `Scrappy.bat`.

    Es el unico sitio que pide escuchar Telegram: aqui hay una persona
    delante que espera que sus botones respondan.
    """
    ScrappyTUI(settings, escuchar=True).run()


__all__ = ["ScrappyTUI", "StatusBar", "run_tui"]
