"""Avisar por Telegram de lo que pasa fuera de Telegram.

De momento solo del arranque, que es el que hacia falta: con el autoarranque
puesto, Scrappy se levanta sin ventana y sin nadie mirando, y hasta ahora la
unica forma de saber si habia arrancado era abrir el log o preguntarle con
`/start`. Despues de reiniciar el equipo, eso es justo lo que uno no quiere
tener que hacer.

**El aviso va a los administradores, nunca al canal.** El canal es para el
contenido; meterle «he arrancado» seria ruido para todo el que lo siga. Si un
administrador no ha hablado nunca con el bot, Telegram no deja escribirle
primero: eso no es un fallo del arranque y por eso aqui no levanta nada, solo
queda en el log.
"""

from __future__ import annotations

import html

from telegram.constants import ParseMode
from telegram.error import TelegramError

from scrappy import __version__
from scrappy.app import ScrappyApp
from scrappy.bot.handlers import SchedulerProtocol, linea_scheduler
from scrappy.core.models import utcnow
from scrappy.core.tiempo import formato_local
from scrappy.observability.logging import get_logger

log = get_logger(__name__)


def texto_arranque(
    app: ScrappyApp,
    scheduler: SchedulerProtocol | None,
    *,
    escuchando: bool,
    motivo: str = "",
) -> str:
    """Lo que se manda. Separado del envio para poder leerlo en un test.

    Dice cuando arranco y cuando publicara, que son las dos preguntas que uno
    se hace al ver el aviso: la hora confirma que fue este reinicio y no el de
    ayer, y la proxima ronda dice si hay que esperar o no.

    Args:
        motivo: si viene, esto no es un arranque sino una recarga, y el motivo
            es el cambio que la provoco. Se dice porque cierra el circulo: quien
            acaba de apagar una fuente desde el movil quiere leer que se apago,
            no un «en marcha» generico que podria ser de cualquier cosa.
    """
    if motivo:
        cabecera = f"🔄 <b>Scrappy recargado</b> · v{html.escape(__version__)}"
        segunda = f"Motivo: {html.escape(motivo)}"
    else:
        cabecera = f"✅ <b>Scrappy en marcha</b> · v{html.escape(__version__)}"
        segunda = f"Arrancado: <b>{html.escape(formato_local(utcnow(), app.settings.tzinfo))}</b>"

    lineas = [cabecera, segunda, linea_scheduler(app, scheduler)]
    if not escuchando:
        # Merece decirse: los botones de las publicaciones no responderan, y
        # sin esto pareceria que el bot esta roto.
        lineas.append("⚠️ No escucho comandos en este arranque.")
    return "\n".join(lineas)


async def avisar_arranque(
    app: ScrappyApp,
    scheduler: SchedulerProtocol | None = None,
    *,
    escuchando: bool = True,
    motivo: str = "",
) -> int:
    """Avisa a cada administrador. Devuelve a cuantos les llego.

    Nunca levanta: un aviso que no sale no puede tumbar el arranque, que es lo
    que de verdad importa que siga en pie.

    Con `motivo` el mensaje es el de una recarga. Ese si se manda aunque los
    avisos de arranque esten apagados: no es ruido periodico, es la respuesta a
    algo que el usuario acaba de pedir desde el movil, y quedarse sin
    contestacion despues de pulsar un boton es peor que un mensaje de mas.
    """
    if not app.settings.notify_on_start and not motivo:
        return 0

    texto = texto_arranque(app, scheduler, escuchando=escuchando, motivo=motivo)
    return await avisar_a_admins(app, texto)


async def avisar_a_admins(app: ScrappyApp, texto: str) -> int:
    """Manda un texto a cada administrador. Devuelve a cuantos les llego.

    **Nunca levanta y nunca va al canal.** El canal es para el contenido;
    meterle avisos de mantenimiento seria ruido para todo el que lo siga.

    Lo comparten el aviso de arranque y el de la sesion de X caducada, que son
    los dos casos en los que pasa algo fuera de Telegram y hay que enterarse
    desde el movil.
    """
    if app.bot is None:
        log.debug("aviso_sin_bot")
        return 0

    admins = app.settings.admin_ids
    if not admins:
        log.info(
            "aviso_sin_destino",
            detalle="SCRAPPY_TELEGRAM_ADMIN_IDS esta vacio y el aviso no va al canal",
        )
        return 0

    enviados = 0
    for admin in sorted(admins):
        try:
            await app.bot.send_message(
                chat_id=admin,
                text=texto,
                parse_mode=ParseMode.HTML,
                disable_notification=False,
            )
        except TelegramError as exc:
            # Lo normal aqui es «chat not found»: ese administrador no le ha
            # escrito nunca al bot, y Telegram no deja empezar la conversacion
            # desde el otro lado.
            log.warning("aviso_fallido", admin=admin, error=str(exc))
            continue
        enviados += 1

    if enviados:
        log.info("aviso_enviado", destinatarios=enviados)
    return enviados


__all__ = ["avisar_a_admins", "avisar_arranque", "texto_arranque"]
