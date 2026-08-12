"""Botones que acompanan a cada publicacion.

Vive en `delivery/` y no en `bot/` por la direccion de las dependencias: `bot/`
ya importa de `delivery/` (a traves de `ScrappyApp`), asi que al reves seria
invertir el sentido. Y conceptualmente encaja: es parte de como se presenta un
medio publicado, igual que el caption.

Las constantes de accion se comparten con `bot/callbacks.py`, que es quien
interpreta las pulsaciones.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

#: Separador de los campos de `callback_data`.
SEP = ":"

# Cortas a proposito: `callback_data` no admite mas de 64 bytes y cada uno
# cuenta contra el identificador del item.
ACCION_BORRAR = "del"
ACCION_VETAR = "ban"
ACCION_DISLIKE = "dis"

#: Limite duro de Telegram para `callback_data`.
LIMITE_CALLBACK = 64


def acciones_de_publicacion(source: str, source_id: str) -> InlineKeyboardMarkup | None:
    """Botones bajo cada meme publicado.

    Devuelve None si el identificador no cabe en el limite de 64 bytes. Es raro,
    pero con ids largos es posible, y mas vale publicar sin botones que que
    Telegram rechace el mensaje entero.
    """
    sufijo = f"{SEP}{source}{SEP}{source_id}"
    if len(f"{ACCION_BORRAR}{sufijo}".encode()) > LIMITE_CALLBACK:
        return None

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Borrar", callback_data=f"{ACCION_BORRAR}{sufijo}"),
                InlineKeyboardButton("🚫 Vetar autor", callback_data=f"{ACCION_VETAR}{sufijo}"),
                InlineKeyboardButton("👎", callback_data=f"{ACCION_DISLIKE}{sufijo}"),
            ]
        ]
    )
