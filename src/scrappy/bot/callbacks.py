"""Atiende las pulsaciones de los teclados inline.

Todo lo que llega aqui viene en `callback_data`, con el formato corto que
define `keyboards.py`. Dos reglas que se cumplen sin excepcion:

- **Siempre se responde al callback**, aunque sea con un mensaje vacio. Si no,
  Telegram deja el boton con el reloj girando indefinidamente y parece colgado.
- **Se comprueba quien pulsa.** Los botones viajan dentro del mensaje, asi que
  en un canal cualquiera podria pulsarlos: la autorizacion no puede depender de
  quien recibio el teclado.
"""

from __future__ import annotations

import html
from datetime import timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from scrappy.app import ScrappyApp
from scrappy.bot.keyboards import (
    ACCION_CANCELAR,
    ACCION_FETCH,
    ACCION_FETCH_MENU,
    ACCION_STATS,
    SEP,
    menu_cantidad,
)
from scrappy.core.models import utcnow
from scrappy.observability.logging import get_logger

log = get_logger(__name__)


def _app(context: ContextTypes.DEFAULT_TYPE) -> ScrappyApp:
    app = context.bot_data.get("scrappy_app")
    if app is None:  # pragma: no cover - error de programacion
        raise RuntimeError("ScrappyApp no esta en bot_data")
    return app  # type: ignore[no-any-return]


def _autorizado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Los botones viajan en el mensaje; hay que comprobar quien pulsa."""
    user = update.effective_user
    admins: frozenset[int] = context.bot_data.get("admin_ids", frozenset())
    return user is not None and user.id in admins


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Punto de entrada de todos los botones."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    if not _autorizado(update, context):
        await query.answer("No estas autorizado.", show_alert=True)
        log.warning("unauthorized_callback", user_id=getattr(update.effective_user, "id", None))
        return

    partes = query.data.split(SEP)
    accion = partes[0]

    try:
        match accion:
            case _ if accion == ACCION_CANCELAR:
                await query.answer()
                await query.edit_message_text("Cancelado.")
            case _ if accion == ACCION_FETCH_MENU:
                await _elegir_cantidad(update, context, partes)
            case _ if accion == ACCION_FETCH:
                await _publicar(update, context, partes)
            case _ if accion == ACCION_STATS:
                await _stats(update, context, partes)
            case _:
                await query.answer("No se que hacer con ese boton.", show_alert=True)
    except Exception as exc:  # el bot no puede morir por un boton
        log.exception("callback_failed", accion=accion, error=str(exc))
        await query.answer(f"Fallo: {exc}"[:200], show_alert=True)


async def _elegir_cantidad(
    update: Update, _context: ContextTypes.DEFAULT_TYPE, partes: list[str]
) -> None:
    """Segundo paso de `/fetch`: ya hay fuente, falta cuantos."""
    query = update.callback_query
    assert query is not None
    await query.answer()

    fuente = partes[1] if len(partes) > 1 else "*"
    etiqueta = "todas las fuentes" if fuente == "*" else fuente

    await query.edit_message_text(
        f"Cuantos items publico de <b>{html.escape(etiqueta)}</b>?",
        parse_mode=ParseMode.HTML,
        reply_markup=menu_cantidad(fuente),
    )


async def _publicar(update: Update, context: ContextTypes.DEFAULT_TYPE, partes: list[str]) -> None:
    """Ejecuta el pipeline con lo elegido en los botones."""
    query = update.callback_query
    assert query is not None

    fuente = partes[1] if len(partes) > 1 else "*"
    try:
        cuantos = int(partes[2]) if len(partes) > 2 else 1
    except ValueError:
        cuantos = 1

    await query.answer("En marcha")
    etiqueta = "todas las fuentes" if fuente == "*" else fuente
    await query.edit_message_text(f"Buscando en {html.escape(etiqueta)}…")

    app = _app(context)
    if fuente != "*" and fuente not in {a.name for a in app.adapters}:
        await query.edit_message_text(
            f"La fuente «{html.escape(fuente)}» no esta activa. Mira /sources."
        )
        return

    report = await app.run_pipeline(limit=cuantos)

    lineas = [f"<b>{html.escape(report.summary_line())}</b>"]
    if report.errors:
        lineas.append("\nIncidencias:")
        lineas += [f"· {html.escape(err)}" for err in report.errors[:3]]
    await query.edit_message_text("\n".join(lineas), parse_mode=ParseMode.HTML)


async def _stats(update: Update, context: ContextTypes.DEFAULT_TYPE, partes: list[str]) -> None:
    """Estadisticas en el rango elegido."""
    query = update.callback_query
    assert query is not None
    await query.answer()

    try:
        dias = int(partes[1]) if len(partes) > 1 else 7
    except ValueError:
        dias = 7

    app = _app(context)
    desde = None if dias == 0 else utcnow() - timedelta(days=dias)
    counts = await app.state.stats(since=desde)
    total = await app.state.total_published()

    titulo = "Historico completo" if dias == 0 else f"Ultimos {dias} dia(s)"
    if not counts:
        await query.edit_message_text(
            f"<b>{titulo}</b>\nNada publicado.\n\nHistorico total: {total}",
            parse_mode=ParseMode.HTML,
        )
        return

    cuerpo = "\n".join(f"· {fuente}: {n}" for fuente, n in sorted(counts.items()))
    await query.edit_message_text(
        f"<b>{titulo}</b>\n{cuerpo}\n\nHistorico total: {total}",
        parse_mode=ParseMode.HTML,
    )
