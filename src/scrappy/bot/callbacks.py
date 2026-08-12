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
    ACCION_BORRAR,
    ACCION_CANCELAR,
    ACCION_DISLIKE,
    ACCION_FETCH,
    ACCION_FETCH_MENU,
    ACCION_STATS,
    ACCION_VETAR,
    SEP,
    menu_cantidad,
)
from scrappy.core.errors import ConfigError
from scrappy.core.models import PublishedItem, utcnow
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
            case _ if accion in (ACCION_BORRAR, ACCION_VETAR, ACCION_DISLIKE):
                await _accion_sobre_publicacion(update, context, accion, partes)
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


async def _accion_sobre_publicacion(
    update: Update, context: ContextTypes.DEFAULT_TYPE, accion: str, partes: list[str]
) -> None:
    """Borrar, vetar al autor o votar en contra de un item ya publicado.

    El `callback_data` solo lleva `fuente:id` porque no cabe mas, asi que el
    resto —empezando por el autor— se busca en el estado.
    """
    query = update.callback_query
    assert query is not None

    if len(partes) < 3:
        await query.answer("Boton mal formado.", show_alert=True)
        return

    source, source_id = partes[1], partes[2]
    app = _app(context)
    item = await app.state.find(source, source_id)

    if item is None:
        # Pasa con `STATE_BACKEND=memory` tras reiniciar, o si se limpio la base.
        await query.answer(
            "No encuentro ese item en el historial; puede que se haya reiniciado "
            "el estado. El mensaje sigue ahi.",
            show_alert=True,
        )
        return

    match accion:
        case _ if accion == ACCION_BORRAR:
            await _borrar(update, context, item)
        case _ if accion == ACCION_VETAR:
            await _vetar_autor(update, context, item)
        case _ if accion == ACCION_DISLIKE:
            await _no_me_gusta(update, context, item)


async def _borrar(update: Update, context: ContextTypes.DEFAULT_TYPE, item: PublishedItem) -> None:
    """Borra el mensaje del canal.

    No se borra del historial de deduplicacion a proposito: si se borrase,
    el mismo meme volveria a colarse en la siguiente ronda.
    """
    query = update.callback_query
    assert query is not None

    from telegram.error import TelegramError

    app = _app(context)
    # Se borra por id en vez de con `query.message.delete()`: el mensaje que
    # acompana al callback puede llegar como inaccesible, y el id ya lo
    # guardamos al publicar.
    message_id = item.telegram_message_id
    if message_id is None:
        await query.answer("De este item no se guardo el id del mensaje.", show_alert=True)
        return

    try:
        await context.bot.delete_message(
            chat_id=app.settings.telegram_target_chat_id, message_id=message_id
        )
        await query.answer("Borrado")
    except TelegramError as exc:
        # Telegram solo deja borrar mensajes de menos de 48 horas.
        await query.answer(
            f"No se pudo borrar: {exc}. Telegram no permite borrar mensajes de mas de 48 horas.",
            show_alert=True,
        )
        return

    await app.state.record_feedback(item, "deleted")


async def _vetar_autor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, item: PublishedItem
) -> None:
    """Anade el autor a `filters.blocked_authors` de `sources.yaml`.

    Se escribe en el fichero, no solo en memoria, para que el veto sobreviva a
    un reinicio. Reutiliza el editor que preserva los comentarios.
    """
    query = update.callback_query
    assert query is not None

    if not item.author:
        await query.answer(
            "De este item no se guardo el autor, asi que no puedo vetarlo. "
            "Pasa con lo publicado antes de esta version.",
            show_alert=True,
        )
        return

    app = _app(context)

    try:
        from scrappy.tui.yaml_editor import SourcesYamlEditor

        editor = SourcesYamlEditor(app.settings.sources_config_path)
        editor.load()
        vetados = list(editor.get_value(["filters", "blocked_authors"], []) or [])

        if item.author.lower() in {str(a).lower() for a in vetados}:
            await query.answer(f"«{item.author}» ya estaba vetado.")
            return

        vetados.append(item.author)
        if not editor.set_value(["filters", "blocked_authors"], vetados):
            await query.answer(
                "No hay seccion `filters.blocked_authors` en sources.yaml.",
                show_alert=True,
            )
            return

        app.sources_config = editor.save()
    except ConfigError as exc:
        await query.answer(f"No se pudo guardar: {exc}"[:200], show_alert=True)
        return

    await app.state.record_feedback(item, "banned_author")
    await query.answer(f"«{item.author}» vetado. No volvera a aparecer.", show_alert=True)


async def _no_me_gusta(
    update: Update, context: ContextTypes.DEFAULT_TYPE, item: PublishedItem
) -> None:
    """Registra un voto en contra.

    No borra ni veta: penaliza a ese autor en el ranking, de forma proporcional
    a cuantos votos negativos acumule. Es la version suave del veto.
    """
    query = update.callback_query
    assert query is not None

    app = _app(context)
    await app.state.record_feedback(item, "dislike")

    votos = (await app.state.disliked_authors()).get(item.author, 0)
    if item.author and votos:
        await query.answer(f"Anotado. «{item.author}» acumula {votos} voto(s) en contra.")
    else:
        await query.answer("Anotado.")


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
