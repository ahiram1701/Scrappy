"""Comandos del bot.

Todos requieren ser administrador. Los que disparan trabajo largo (`/fetch`)
responden de inmediato y luego editan su propio mensaje con el resultado, para
que Telegram no de el comando por perdido mientras se descarga un video.

Cada handler es delgado a proposito: traduce el mensaje a una llamada de
`ScrappyApp` y formatea la respuesta. La logica esta en el pipeline.
"""

from __future__ import annotations

import html
from datetime import timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scrappy.app import ScrappyApp
from scrappy.bot.auth import admin_only, parse_args
from scrappy.core.models import utcnow
from scrappy.download.workspace import iter_active, purge_active
from scrappy.observability.logging import get_logger
from scrappy.sources.registry import iter_adapter_names

log = get_logger(__name__)

_HELP = """<b>Scrappy</b> — curador de videos cortos y memes

/fetch [fuente] [n] — busca y publica ahora mismo
/sources — estado de cada fuente
/stats [dias] — que se ha publicado
/pause · /resume — para y reanuda el scheduler
/config — configuracion efectiva (sin secretos)
/health — diagnostico del sistema
/purge — borra los ficheros temporales ahora
/help — este mensaje

El contenido descargado nunca se guarda en la maquina: se publica aqui y se
borra. Telegram es el unico archivo."""


def _app(context: ContextTypes.DEFAULT_TYPE) -> ScrappyApp:
    """Recupera la aplicacion del `bot_data` donde la dejo el arranque."""
    app = context.bot_data.get("scrappy_app")
    if app is None:  # pragma: no cover - error de programacion
        raise RuntimeError("ScrappyApp no esta en bot_data")
    return app  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
@admin_only
async def cmd_start(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(_HELP, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/fetch [fuente] [n]` — ejecuta el pipeline al momento."""
    message = update.effective_message
    if message is None:
        return

    app = _app(context)
    args = parse_args(context)

    source: str | None = None
    limit: int | None = None
    known = set(iter_adapter_names())

    for arg in args:
        if arg.lower() in known:
            source = arg.lower()
        elif arg.isdigit():
            limit = max(int(arg), 1)
        else:
            await message.reply_text(
                f"No entiendo «{html.escape(arg)}». Uso: /fetch [{'|'.join(sorted(known))}] [n]"
            )
            return

    if source is not None and source not in {a.name for a in app.adapters}:
        await message.reply_text(f"La fuente «{source}» no esta activa ahora mismo. Mira /sources.")
        return

    notice = await message.reply_text("Buscando…")

    try:
        report = await app.run_pipeline(limit=limit)
    except Exception as exc:  # el bot no debe morir porque un run falle
        log.exception("fetch_failed", error=str(exc))
        await notice.edit_text(f"El run fallo: {html.escape(str(exc))}")
        return

    lines = [f"<b>{html.escape(report.summary_line())}</b>"]
    if report.published:
        lines.append(f"Publicados {len(report.published)} items.")
    if report.errors:
        shown = report.errors[:5]
        lines.append("\nIncidencias:")
        lines += [f"· {html.escape(err)}" for err in shown]
        if len(report.errors) > len(shown):
            lines.append(f"· …y {len(report.errors) - len(shown)} mas")

    await notice.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/sources` — que fuentes hay y en que estado."""
    message = update.effective_message
    if message is None:
        return
    statuses = await _app(context).source_statuses()
    body = "\n".join(f"· {html.escape(status.render())}" for status in statuses)
    await message.reply_text(f"<b>Fuentes</b>\n{body}", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/stats [dias]` — publicaciones por fuente en una ventana de tiempo."""
    message = update.effective_message
    if message is None:
        return

    args = parse_args(context)
    days = int(args[0]) if args and args[0].isdigit() else 7
    since = utcnow() - timedelta(days=days)

    app = _app(context)
    counts = await app.state.stats(since=since)
    total = await app.state.total_published()

    if not counts:
        await message.reply_text(
            f"Nada publicado en los ultimos {days} dias. Historico: {total} items."
        )
        return

    body = "\n".join(f"· {source}: {count}" for source, count in sorted(counts.items()))
    await message.reply_text(
        f"<b>Ultimos {days} dias</b>\n{body}\n\nHistorico total: {total}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    app.paused = True
    if update.effective_message:
        await update.effective_message.reply_text(
            "Scheduler en pausa. `/fetch` sigue funcionando. Usa /resume para reanudar."
        )


@admin_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    app.paused = False
    if update.effective_message:
        await update.effective_message.reply_text(
            f"Scheduler reanudado (cada {app.settings.schedule_interval_minutes} min)."
        )


@admin_only
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/config` — configuracion efectiva, con los secretos ya redactados."""
    message = update.effective_message
    if message is None:
        return
    config = _app(context).effective_config()
    body = "\n".join(f"{key} = {value}" for key, value in config.items())
    await message.reply_text(
        f"<b>Configuracion efectiva</b>\n<pre>{html.escape(body)}</pre>",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    report = await _app(context).health()
    header = "Todo correcto" if report.ok else "Hay algo que revisar"
    await message.reply_text(
        f"<b>{header}</b>\n<pre>{html.escape(report.render())}</pre>",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_purge(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/purge` — borra ya los workspaces temporales.

    En condiciones normales no hace falta: cada item borra el suyo al terminar.
    Existe como red de seguridad y para poder comprobar la garantia a mano.
    """
    message = update.effective_message
    if message is None:
        return
    before = len(list(iter_active()))
    removed = purge_active()
    await message.reply_text(
        f"Workspaces activos antes: {before}. Borrados: {removed}. "
        "El disco queda sin contenido descargado."
    )


async def on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler global: un fallo en un comando no debe tumbar el bot."""
    if context.error is None:
        return
    log.exception("handler_error", error=str(context.error))


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def register_handlers(application: Application, app: ScrappyApp) -> None:  # type: ignore[type-arg]
    """Conecta los comandos y deja `ScrappyApp` accesible en `bot_data`."""
    application.bot_data["scrappy_app"] = app
    application.bot_data["admin_ids"] = app.settings.admin_ids

    application.add_handler(CommandHandler(["start", "help"], cmd_start))
    application.add_handler(CommandHandler("fetch", cmd_fetch))
    application.add_handler(CommandHandler("sources", cmd_sources))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("config", cmd_config))
    application.add_handler(CommandHandler("health", cmd_health))
    application.add_handler(CommandHandler("purge", cmd_purge))
    application.add_error_handler(on_error)

    if not app.settings.admin_ids:
        log.warning(
            "no_admins_configured",
            detail="SCRAPPY_TELEGRAM_ADMIN_IDS esta vacio: nadie podra usar comandos",
        )
