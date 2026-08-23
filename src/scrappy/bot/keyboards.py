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

# Configurar fuentes. Los argumentos son **posiciones**, no nombres: una
# consulta de Bluesky ocupa ella sola mas de la mitad de los 64 bytes.
ACCION_FUENTES = "sc"
ACCION_FUENTE = "sf"
ACCION_INTERRUPTOR = "so"
ACCION_ORIGENES = "sl"
ACCION_ANADIR = "sa"
ACCION_QUITAR = "sq"
ACCION_QUITAR_OK = "sqc"
ACCION_INTERRUPTOR_OK = "soc"
ACCION_COOKIES = "sck"

__all__ = [
    "ACCION_ANADIR",
    "ACCION_BORRAR",
    "ACCION_CANCELAR",
    "ACCION_COOKIES",
    "ACCION_DISLIKE",
    "ACCION_FETCH",
    "ACCION_FETCH_MENU",
    "ACCION_FUENTE",
    "ACCION_FUENTES",
    "ACCION_INTERRUPTOR",
    "ACCION_INTERRUPTOR_OK",
    "ACCION_ORIGENES",
    "ACCION_QUITAR",
    "ACCION_QUITAR_OK",
    "ACCION_STATS",
    "ACCION_VETAR",
    "SEP",
    "acciones_de_publicacion",
    "confirmar_encender_arriesgada",
    "confirmar_quitar",
    "ficha_fuente",
    "lista_origenes",
    "menu_cantidad",
    "menu_fetch",
    "menu_fuentes",
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


# ---------------------------------------------------------------------------
# Configurar fuentes
# ---------------------------------------------------------------------------
def menu_fuentes(estados: list[tuple[str, bool, bool, bool]]) -> InlineKeyboardMarkup:
    """Las nueve fuentes, con su estado a la vista.

    Args:
        estados: `(nombre, encendida, bloqueada_por_tos, arriesgada)` en el
            orden en que se quieren pintar.

    El emoji no es decoracion: es lo unico que distingue de un vistazo «apagada
    porque no la quiero» de «apagada porque incumple los terminos y hace falta
    aceptarlo a mano», y son dos situaciones que se arreglan de forma distinta.

    El ⚠️ existe por un agujero que se veia venir: el candado depende de que
    `ENABLE_TOS_RISKY_SOURCES` este apagado, asi que **en cuanto alguien activa
    ese flag las fuentes de riesgo pasaban a verse identicas a Reddit**. Quien
    lo activo hace tres meses ya no se acuerda. La marca sobrevive al flag.
    """
    filas = []
    for i in range(0, len(estados), 2):
        fila = []
        for nombre, encendida, bloqueada, arriesgada in estados[i : i + 2]:
            if bloqueada:
                marca = "🔒"
            elif encendida:
                marca = "⚠️" if arriesgada else "✅"
            else:
                marca = "⚪"
            fila.append(
                InlineKeyboardButton(
                    f"{marca} {nombre}", callback_data=f"{ACCION_FUENTE}{SEP}{nombre}"
                )
            )
        filas.append(fila)

    filas.append([InlineKeyboardButton("Cerrar", callback_data=ACCION_CANCELAR)])
    return InlineKeyboardMarkup(filas)


def ficha_fuente(
    fuente: str,
    *,
    encendida: bool,
    campos: tuple[tuple[str, str], ...],
    cuantos: dict[str, int | None],
    renovable: bool = False,
) -> InlineKeyboardMarkup:
    """Interruptor y campos de origen de una fuente.

    Args:
        cuantos: cuantos valores tiene cada campo, o None si el campo no esta
            en el YAML. Se distingue porque el editor no crea claves que no
            existan, y ofrecer «anadir» donde no se va a poder escribir seria
            prometer algo que no se cumple.
        renovable: si esa fuente tiene una sesion que se pueda renovar. Hoy
            solo X con el backend `scrape`. Un boton que no aplica es peor que
            no tener boton, asi que no se pinta para las demas.
    """
    filas = [
        [
            InlineKeyboardButton(
                "Apagar" if encendida else "Encender",
                callback_data=f"{ACCION_INTERRUPTOR}{SEP}{fuente}{SEP}{0 if encendida else 1}",
            )
        ]
    ]

    for indice, (clave, etiqueta) in enumerate(campos):
        total = cuantos.get(clave)
        if total is None:
            aviso = InlineKeyboardButton(
                f"{etiqueta}: no esta en el YAML", callback_data=ACCION_CANCELAR
            )
            filas.append([aviso])
            continue
        filas.append(
            [
                InlineKeyboardButton(
                    f"{etiqueta} ({total})",
                    callback_data=f"{ACCION_ORIGENES}{SEP}{fuente}{SEP}{indice}",
                )
            ]
        )

    if renovable:
        filas.append(
            [
                InlineKeyboardButton(
                    "🔄 Renovar cookies", callback_data=f"{ACCION_COOKIES}{SEP}{fuente}"
                )
            ]
        )

    filas.append([InlineKeyboardButton("‹ Fuentes", callback_data=ACCION_FUENTES)])
    return InlineKeyboardMarkup(filas)


def lista_origenes(
    fuente: str, campo: int, valores: list[str], *, se_puede_anadir: bool
) -> InlineKeyboardMarkup:
    """Un boton por valor para quitarlo, mas «anadir».

    Los valores se truncan a 22 caracteres: una consulta de Bluesky entera no
    cabe en un boton de movil, y lo que hace falta es reconocerla, no leerla.
    """
    filas = []
    for indice, valor in enumerate(valores):
        etiqueta = valor if len(valor) <= 22 else f"{valor[:21]}…"
        filas.append(
            [
                InlineKeyboardButton(
                    f"❌ {etiqueta}",
                    callback_data=f"{ACCION_QUITAR}{SEP}{fuente}{SEP}{campo}{SEP}{indice}",
                )
            ]
        )

    ultima = []
    if se_puede_anadir:
        ultima.append(
            InlineKeyboardButton(
                "➕ Anadir", callback_data=f"{ACCION_ANADIR}{SEP}{fuente}{SEP}{campo}"
            )
        )
    ultima.append(InlineKeyboardButton("‹ Volver", callback_data=f"{ACCION_FUENTE}{SEP}{fuente}"))
    filas.append(ultima)
    return InlineKeyboardMarkup(filas)


def confirmar_encender_arriesgada(fuente: str) -> InlineKeyboardMarkup:
    """Encender una fuente que incumple los ToS pide un segundo toque.

    Mientras `ENABLE_TOS_RISKY_SOURCES` estaba apagado, el propio flag hacia de
    confirmacion: habia que ir al `.env` y escribirlo a mano. En cuanto se
    activa -y se activa una vez, para siempre- encender TikTok pasaba a ser un
    toque al lado de encender Reddit. Esto devuelve el paso que se perdio.

    Apagar no confirma nunca: retirarse siempre es seguro y tiene que ser
    inmediato.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Si, encender",
                    callback_data=f"{ACCION_INTERRUPTOR_OK}{SEP}{fuente}",
                ),
                InlineKeyboardButton("Cancelar", callback_data=f"{ACCION_FUENTE}{SEP}{fuente}"),
            ]
        ]
    )


def confirmar_quitar(fuente: str, campo: int, indice: int) -> InlineKeyboardMarkup:
    """Quitar pide confirmacion, como todo lo que no se puede deshacer.

    Ademas cubre el caso feo de los indices: si el YAML cambio entre pintar y
    pulsar, aqui se ve el valor releido y se puede cancelar.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Si, quitar",
                    callback_data=f"{ACCION_QUITAR_OK}{SEP}{fuente}{SEP}{campo}{SEP}{indice}",
                ),
                InlineKeyboardButton(
                    "Cancelar", callback_data=f"{ACCION_ORIGENES}{SEP}{fuente}{SEP}{campo}"
                ),
            ]
        ]
    )
