"""Escuchar comandos y botones de Telegram.

Publicar y escuchar son dos cosas distintas, y esa distincion costo un fallo:
`ScrappyApp` construye un `Bot` con el que **enviar**, pero recibir necesita
ademas un `Application` haciendo *polling*. Solo `scrappy run` lo montaba, asi
que desde la TUI se publicaba con normalidad y ningun comando ni boton
respondia: los mensajes llegaban al canal y sus botones no tenian a quien
preguntar.

Este modulo es ese trozo, en un solo sitio, para que las dos formas de
arrancar Scrappy escuchen igual.

## Dos procesos no pueden escuchar a la vez

Telegram solo entrega cada actualizacion una vez: si dos procesos consultan
con el mismo token, responde 409 a uno de ellos. Pasa en cuanto se tiene el
arranque automatico puesto y ademas se abre la TUI, que no es un caso raro.

No se puede impedir -son procesos distintos, quiza en maquinas distintas- pero
si detectarlo y decirlo, que es lo que hace `conflicto`. Callarse dejaria dos
Scrappys peleandose por cada pulsacion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.error import Conflict, TelegramError
from telegram.ext import Application, ApplicationBuilder

from scrappy.app import ScrappyApp
from scrappy.bot.handlers import SchedulerProtocol, register_handlers
from scrappy.bot.recarga import Recargador
from scrappy.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger(__name__)


class BotListener:
    """Mantiene un `Application` de python-telegram-bot escuchando."""

    def __init__(
        self,
        app: ScrappyApp,
        scheduler: SchedulerProtocol | None = None,
        *,
        on_conflict: Callable[[str], None] | None = None,
        recargador: Recargador | None = None,
    ) -> None:
        """
        Args:
            on_conflict: se llama si otro proceso esta escuchando con el mismo
                token. La TUI lo usa para avisar por pantalla; sin el, solo
                queda en el log.
            recargador: como pedir que Scrappy se reconstruya. Se pasa aqui y
                no se guarda en un sitio global porque cada listener nuevo
                estrena su propio `bot_data`: quien monta el listener es quien
                sabe como se recarga a si mismo.
        """
        self._app = app
        self._scheduler = scheduler
        self._on_conflict = on_conflict
        self._recargador = recargador
        self._application: Application | None = None  # type: ignore[type-arg]
        #: Motivo por el que dejo de escuchar, si dejo de hacerlo.
        self.conflicto: str | None = None

    @property
    def running(self) -> bool:
        return self._application is not None

    async def start(self) -> bool:
        """Empieza a escuchar. Devuelve si lo consiguio.

        No levanta: que no se pueda escuchar es un problema, pero no uno que
        deba impedir publicar. La TUI seguiria siendo util sin comandos.
        """
        token = self._app.settings.telegram_bot_token.get_secret_value()
        if not token:
            return False

        try:
            application = ApplicationBuilder().token(token).build()
            register_handlers(application, self._app, self._scheduler, recargador=self._recargador)

            await application.initialize()
            await application.start()
            if application.updater is not None:
                await application.updater.start_polling(
                    # Lo que llego mientras nadie escuchaba ya no interesa:
                    # procesarlo de golpe al arrancar publicaria varias veces
                    # seguidas por botones pulsados hace horas.
                    drop_pending_updates=True,
                    error_callback=self._error,
                )
        except TelegramError as exc:
            log.warning("bot_listener_failed", error=str(exc))
            return False

        self._application = application
        log.info("bot_listener_started")
        return True

    def _error(self, exc: TelegramError) -> None:
        """Lo llama el updater con los fallos del propio bucle de polling."""
        if not isinstance(exc, Conflict):
            log.warning("bot_polling_error", error=str(exc))
            return

        self.conflicto = (
            "Otro Scrappy ya esta escuchando con este mismo token, asi que los "
            "comandos y los botones los atiende el. Suele ser el arranque "
            "automatico: mira el Panel."
        )
        log.warning("bot_polling_conflict", detail=self.conflicto)
        if self._on_conflict is not None:
            self._on_conflict(self.conflicto)

    async def stop(self) -> None:
        """Deja de escuchar, en el orden inverso al de arranque."""
        application = self._application
        self._application = None
        if application is None:
            return

        try:
            if application.updater is not None and application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
            await application.shutdown()
        except Exception as exc:  # el apagado no puede impedir cerrar
            log.warning("bot_listener_stop_failed", error=str(exc))
        else:
            log.info("bot_listener_stopped")


__all__ = ["BotListener"]
