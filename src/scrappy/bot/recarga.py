"""Pedir desde Telegram que Scrappy se recargue.

Cambiar un ajuste del `.env` no sirve de nada hasta que la aplicacion se
reconstruye: los ajustes se leen una sola vez y se reparten por los adapters,
el cliente HTTP y el backend de estado. Eso ya lo resuelve la TUI con su boton
«Recargar»; este modulo es la forma de pedirlo desde un handler del bot.

## Por que el encargo es sincrono

Recargar implica parar el `Application` de Telegram, y `Application.stop()`
**espera a que terminen los handlers en curso**. Si un handler pudiera hacer
`await recargar()`, se estaria esperando a si mismo: el bot se quedaria colgado
para siempre y sin un solo mensaje de error.

La proteccion es la firma. `Callable[[str], bool]`, sin `async`: awaitarlo no
se puede escribir, asi que el deadlock no se puede programar por descuido. Lo
unico que hace es encolar el trabajo -un `Event.set()` en `scrappy run`, un
`call_later` en la TUI- y devolver de inmediato.

## Y por que no un `asyncio.Event` a secas

Un evento obliga a que alguien este esperandolo en un `await event.wait()`.
Eso encaja en `scrappy run`, que es dueno de su bucle, y no en la TUI, cuyo
bucle lo lleva Textual. Un callable deja que cada uno lo implemente como le
corresponde con un solo contrato compartido.
"""

from __future__ import annotations

from collections.abc import Callable

from telegram.ext import ContextTypes

from scrappy.observability.logging import get_logger

log = get_logger(__name__)

#: Donde vive el encargo dentro de `bot_data`.
CLAVE = "solicitar_recarga"

#: Recibe el motivo -para el aviso de vuelta- y devuelve si se acepto.
Recargador = Callable[[str], bool]


def pedir_recarga(context: ContextTypes.DEFAULT_TYPE, motivo: str) -> bool:
    """Pide una recarga. Devuelve si hay alguien que pueda atenderla.

    Que devuelva `False` no es un error: `scrappy run --no-bot`, un montaje
    ajeno o un test pueden no registrar ninguno. Quien llama tiene que decir la
    verdad en ese caso -«se aplicara al reiniciar»- en vez de prometer que ya
    esta hecho.
    """
    recargador: Recargador | None = context.bot_data.get(CLAVE)
    if recargador is None:
        log.info("recarga_no_disponible", motivo=motivo)
        return False

    aceptada = recargador(motivo)
    log.info("recarga_pedida", motivo=motivo, aceptada=aceptada)
    return aceptada


__all__ = ["CLAVE", "Recargador", "pedir_recarga"]
