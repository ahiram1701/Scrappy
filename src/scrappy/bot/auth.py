"""Autorizacion de los comandos.

El bot publica en un canal que puede ser publico, asi que cualquiera podria
intentar hablarle. Solo los IDs listados en `SCRAPPY_TELEGRAM_ADMIN_IDS` pueden
ejecutar comandos; el resto recibe una negativa seca y queda registrado.

Si la lista esta vacia, nadie puede usar comandos. Es deliberado: un bot recien
configurado no debe quedar abierto a todo el mundo por descuido.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# `Coroutine` y no `Awaitable` porque es lo que espera `CommandHandler`.
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_DENIED = (
    "No estas autorizado a usar este bot.\n"
    "Si eres su administrador, anade tu ID a SCRAPPY_TELEGRAM_ADMIN_IDS."
)


def admin_only(handler: Handler) -> Handler:
    """Decorador que corta el paso a quien no este en la lista de administradores.

    El conjunto de IDs se lee de `context.bot_data` en cada llamada, no se
    captura al registrar, para que recargar la configuracion tenga efecto sin
    reiniciar el proceso.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        admins: frozenset[int] = context.bot_data.get("admin_ids", frozenset())

        if user is None or user.id not in admins:
            log.warning(
                "unauthorized_command",
                user_id=getattr(user, "id", None),
                command=_command_of(update),
            )
            if update.effective_message is not None:
                await update.effective_message.reply_text(_DENIED)
            return

        await handler(update, context)

    return wrapper


def _command_of(update: Update) -> str:
    message = update.effective_message
    if message is None or not message.text:
        return "<sin texto>"
    return message.text.split()[0]


def parse_args(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Argumentos del comando, ya troceados por python-telegram-bot."""
    args: Any = getattr(context, "args", None)
    return [str(item) for item in args] if args else []
