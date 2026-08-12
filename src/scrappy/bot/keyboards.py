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

#: Separador de los campos de `callback_data`.
SEP = ":"

# Acciones. Cortas a proposito: cada byte cuenta contra el limite de 64.
ACCION_FETCH = "f"
ACCION_FETCH_MENU = "fm"
ACCION_STATS = "st"
ACCION_SOURCES = "sr"
ACCION_BORRAR = "del"
ACCION_VETAR = "ban"
ACCION_DISLIKE = "dis"
ACCION_CANCELAR = "x"


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


def acciones_de_publicacion(source: str, source_id: str) -> InlineKeyboardMarkup | None:
    """Botones bajo cada meme publicado.

    Devuelve None si el identificador no cabe en el limite de 64 bytes, que es
    raro pero posible con ids largos: mejor publicar sin botones que que
    Telegram rechace el mensaje entero.
    """
    sufijo = f"{SEP}{source}{SEP}{source_id}"
    if len(f"{ACCION_BORRAR}{sufijo}".encode()) > 64:
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
