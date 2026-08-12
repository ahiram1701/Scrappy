"""Teclados inline del bot.

Escribir `/fetch reddit 3` obliga a recordar la sintaxis y el nombre exacto de
cada fuente. Con botones no hace falta recordar nada, que es lo que se espera
de un bot que se usa desde el movil.

## El limite de 64 bytes

`callback_data` no puede pasar de 64 bytes, asi que aqui no caben permalinks ni
titulos. Se usa un formato corto y estable:

    accion:argumento[:argumento]

Por ejemplo `f:reddit:3` (fetch de reddit, 3 items) o `del:reddit:1r4jnof`
(borrar ese item). `callbacks.py` los interpreta.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Las acciones sobre una publicacion viven en `delivery/keyboards.py`, junto al
# codigo que las adjunta. Se reexportan aqui para que `callbacks.py` tenga un
# solo sitio de donde importar.
from scrappy.delivery.keyboards import (
    ACCION_BORRAR,
    ACCION_DISLIKE,
    ACCION_VETAR,
    SEP,
    acciones_de_publicacion,
)

# Acciones de los menus. Cortas a proposito: cada byte cuenta contra el
# limite de 64 de `callback_data`.
ACCION_FETCH = "f"
ACCION_FETCH_MENU = "fm"
ACCION_STATS = "st"
ACCION_CANCELAR = "x"

__all__ = [
    "ACCION_BORRAR",
    "ACCION_CANCELAR",
    "ACCION_DISLIKE",
    "ACCION_FETCH",
    "ACCION_FETCH_MENU",
    "ACCION_STATS",
    "ACCION_VETAR",
    "SEP",
    "acciones_de_publicacion",
    "menu_cantidad",
    "menu_fetch",
    "menu_stats",
]


def menu_fetch(fuentes: list[str]) -> InlineKeyboardMarkup:
    """Elegir fuente para publicar, o todas."""
    filas = [
        [InlineKeyboardButton("Todas las fuentes", callback_data=f"{ACCION_FETCH_MENU}{SEP}*")]
    ]

    # De dos en dos: en el movil, tres botones por fila quedan ilegibles.
    for i in range(0, len(fuentes), 2):
        filas.append(
            [
                InlineKeyboardButton(fuente, callback_data=f"{ACCION_FETCH_MENU}{SEP}{fuente}")
                for fuente in fuentes[i : i + 2]
            ]
        )

    filas.append([InlineKeyboardButton("Cancelar", callback_data=ACCION_CANCELAR)])
    return InlineKeyboardMarkup(filas)


def menu_cantidad(fuente: str) -> InlineKeyboardMarkup:
    """Cuantos items publicar de la fuente ya elegida."""
    botones = [
        InlineKeyboardButton(str(n), callback_data=f"{ACCION_FETCH}{SEP}{fuente}{SEP}{n}")
        for n in (1, 3, 5, 10)
    ]
    return InlineKeyboardMarkup(
        [botones, [InlineKeyboardButton("Cancelar", callback_data=ACCION_CANCELAR)]]
    )


def menu_stats() -> InlineKeyboardMarkup:
    """Rangos de tiempo para las estadisticas."""
    botones = [
        InlineKeyboardButton(etiqueta, callback_data=f"{ACCION_STATS}{SEP}{dias}")
        for etiqueta, dias in (("Hoy", 1), ("7 dias", 7), ("30 dias", 30), ("Todo", 0))
    ]
    return InlineKeyboardMarkup([botones])
