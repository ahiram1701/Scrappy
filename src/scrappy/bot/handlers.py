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
from typing import Protocol

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from scrappy.app import ScrappyApp
from scrappy.autostart import Autoarranque
from scrappy.bot.auth import admin_only, parse_args
from scrappy.bot.callbacks import on_callback
from scrappy.bot.callbacks_fuentes import on_respuesta
from scrappy.bot.keyboards import menu_fetch, menu_fuentes, menu_stats
from scrappy.bot.recarga import CLAVE as CLAVE_RECARGA
from scrappy.bot.recarga import Recargador
from scrappy.core.models import utcnow
from scrappy.core.tiempo import formato_local, formato_relativo
from scrappy.diagnostics import run_diagnostics
from scrappy.download.workspace import iter_active, purge_active
from scrappy.observability.logging import get_logger
from scrappy.sources.registry import iter_adapter_names, requiere_ack_de_tos

log = get_logger(__name__)

_HELP = """<b>Scrappy</b> — curador de videos cortos y memes

/status — como esta Scrappy, de un vistazo
/fetch [fuente] [n] — busca y publica ahora mismo
/sources — estado de cada fuente, y configurarlas
/stats [dias] — que se ha publicado
/pause · /resume — para y reanuda el scheduler
/config — configuracion efectiva (sin secretos)
/health — diagnostico del sistema
/purge — borra los ficheros temporales ahora
/help — este mensaje

El contenido descargado nunca se guarda en la maquina: se publica aqui y se
borra. Telegram es el unico archivo."""


class SchedulerProtocol(Protocol):
    """Lo unico que el bot necesita saber del scheduler.

    Un protocolo y no `PipelineScheduler` para no atar los handlers a
    APScheduler: aqui solo se lee, y asi los tests pueden pasar cualquier cosa
    que responda a estas tres preguntas.
    """

    @property
    def running(self) -> bool: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def next_run_at(self) -> str | None: ...


def _app(context: ContextTypes.DEFAULT_TYPE) -> ScrappyApp:
    """Recupera la aplicacion del `bot_data` donde la dejo el arranque."""
    app = context.bot_data.get("scrappy_app")
    if app is None:  # pragma: no cover - error de programacion
        raise RuntimeError("ScrappyApp no esta en bot_data")
    return app  # type: ignore[no-any-return]


def _scheduler(context: ContextTypes.DEFAULT_TYPE) -> SchedulerProtocol | None:
    """El scheduler, si quien arranco el bot lo registro."""
    return context.bot_data.get("scheduler")


def linea_scheduler(app: ScrappyApp, scheduler: SchedulerProtocol | None) -> str:
    """Que va a publicar y cuando, en una o dos lineas.

    Lo comparten `/start`, `/health` y el aviso de arranque. Sin scheduler
    registrado se dice la cadencia y no la hora: prometer una hora que no se
    puede consultar seria peor que no darla.
    """
    if not app.settings.schedule_enabled:
        return "El scheduler esta desactivado: solo publicare con /fetch."

    cadencia = (
        f"Publicare {app.settings.items_per_run} items cada "
        f"{app.settings.schedule_interval_minutes} minutos, "
        f"en horario de {html.escape(str(app.settings.tzinfo))}."
    )

    if scheduler is None or not scheduler.running:
        return f"{cadencia}\n⚠️ Ahora mismo no hay rondas programadas."

    if app.paused:
        return f"{cadencia}\n⏸ En pausa. Usa /resume para reanudar."

    proxima = scheduler.next_run_at
    return f"{cadencia}\nProxima ronda: <b>{html.escape(proxima)}</b>." if proxima else cadencia


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/start` — confirma que todo funciona, no solo lista comandos.

    Antes esto escupia la lista de comandos, que es lo que menos falta hace
    justo despues de configurar el bot: lo que uno quiere saber es si le
    alcanza, a donde va a publicar y que fuentes estan listas.
    """
    message = update.effective_message
    if message is None:
        return

    app = _app(context)
    aviso = await message.reply_text("Comprobando la configuracion…")

    diagnosis = await run_diagnostics(app.settings, use_network=False)

    lineas = [f"<b>Hola. Soy Scrappy.</b>\n{html.escape(diagnosis.summary())}\n"]

    # Lo primero que uno quiere confirmar: que el mensaje llego, y a donde ira.
    destino = app.settings.telegram_target_chat_id
    chat_actual = str(message.chat_id)
    if destino == chat_actual:
        lineas.append("✅ Este es el chat donde publicare.")
    else:
        lineas.append(f"ℹ️ Publicare en el chat <code>{html.escape(destino)}</code>, no aqui.")

    listas = [s for s in await app.source_statuses() if s.usable]
    faltan = [s for s in await app.source_statuses() if s.enabled and not s.configured]

    if listas:
        lineas.append(f"\n<b>Fuentes listas ({len(listas)})</b>")
        lineas += [f"· {html.escape(s.name)}" for s in listas]
    else:
        lineas.append("\n⚠️ <b>Ninguna fuente lista.</b> Mira /sources.")

    if faltan:
        lineas.append("\n<b>Activadas pero sin configurar</b>")
        lineas += [f"· {html.escape(s.name)}: {html.escape(s.detail)}" for s in faltan]

    problemas = diagnosis.blocking + diagnosis.warnings
    if problemas:
        lineas.append("\n<b>Por revisar</b>")
        for check in problemas[:5]:
            lineas.append(f"· {html.escape(check.name)}: {html.escape(check.detail)}")
            if check.fix:
                lineas.append(f"  <i>{html.escape(check.fix)}</i>")

    # Cadencia, zona horaria y la hora de la proxima ronda. La zona va aqui
    # porque «me publico de madrugada» es la queja tipica, y verla desde el
    # primer mensaje la explica sin tener que buscarla.
    lineas.append(f"\n{linea_scheduler(app, _scheduler(context))}")

    lineas.append("\nEscribe / para ver todo lo que puedo hacer.")

    await aviso.edit_text("\n".join(lineas), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_help(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
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

    # Sin argumentos se ofrecen botones: recordar la sintaxis exacta y el
    # nombre de cada fuente es justo lo que no deberia hacer falta.
    if not args:
        activas = [adapter.name for adapter in app.adapters]
        if not activas:
            await message.reply_text("No hay ninguna fuente activa. Mira /sources.")
            return
        await message.reply_text("De donde quieres que publique?", reply_markup=menu_fetch(activas))
        return

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
    app = _app(context)
    statuses = await app.source_statuses()
    body = "\n".join(f"· {html.escape(status.render())}" for status in statuses)
    # El teclado es la puerta a configurarlas. El texto sigue siendo el de
    # siempre: quien solo venia a mirar no tiene que leer nada nuevo.
    estados = [
        (
            s.name,
            s.enabled,
            requiere_ack_de_tos(s.name, app.settings) and not app.settings.enable_tos_risky_sources,
            requiere_ack_de_tos(s.name, app.settings),
        )
        for s in statuses
    ]
    await message.reply_text(
        f"<b>Fuentes</b>\n{body}",
        parse_mode=ParseMode.HTML,
        reply_markup=menu_fuentes(estados),
    )


def _motivo(detalle: str) -> str:
    """La primera frase del detalle de una fuente, que es la que dice que falta.

    El resto suele ser como arreglarlo -URL incluida- y eso es trabajo de
    `/sources` y de `/start`, no de un resumen.
    """
    primera = detalle.split(". ")[0].strip()
    return primera if len(primera) <= 70 else f"{primera[:67]}…"


async def texto_status(app: ScrappyApp, scheduler: SchedulerProtocol | None) -> str:
    """Todo el estado en un mensaje. Separado del handler para poder probarlo.

    El orden no es casual: contesta de arriba abajo las preguntas segun lo
    urgentes que sean. Primero «esta vivo?», luego «va a publicar?», luego
    «publico algo?» y al final «que le falta?». Quien mira esto desde el movil
    despues de reiniciar el equipo casi siempre se queda en la primera linea.
    """
    desde = formato_local(app.arrancado_en, app.settings.tzinfo)
    hace = formato_relativo(app.arrancado_en - utcnow())
    lineas = [f"<b>Scrappy</b> · en marcha desde {html.escape(desde)} ({html.escape(hace)})"]

    # Como arranco. Solo en Windows: fuera de ahi el autoarranque es systemd y
    # esto no sabe nada de el, y una linea vacia confunde mas que ayudar.
    autoarranque = Autoarranque().status()
    if autoarranque.disponible and autoarranque.activo:
        cual = (
            "al encender el equipo, sin iniciar sesion"
            if autoarranque.modo == "sistema"
            else "al iniciar sesion"
        )
        lineas.append(f"Arranque automatico: {cual}")

    lineas.append("")
    lineas.append(linea_scheduler(app, scheduler))

    ultima = app.ultima_ronda
    if ultima is None:
        lineas.append("Ultima ronda: aun no ha corrido ninguna.")
    else:
        marca = "" if ultima.correcta else "⚠️ "
        lineas.append(
            f"{marca}Ultima ronda: {html.escape(formato_local(ultima.cuando, app.settings.tzinfo))}"
            f" — {html.escape(ultima.resumen)}"
        )

    hoy = await app.state.stats(since=utcnow() - timedelta(days=1))
    semana = await app.state.stats(since=utcnow() - timedelta(days=7))
    total = await app.state.total_published()
    lineas.append("")
    lineas.append(
        f"Publicado · hoy {sum(hoy.values())} · 7 dias {sum(semana.values())} · historico {total}"
    )

    statuses = await app.source_statuses()
    listas = [s.name for s in statuses if s.usable]
    lineas.append(
        f"Fuentes listas ({len(listas)}): {html.escape(', '.join(listas))}"
        if listas
        else "⚠️ Ninguna fuente lista: no se va a publicar nada."
    )

    # Solo las que estan a medias. Las apagadas a proposito no son un problema
    # que haya que ensenar cada vez.
    a_medias = [s for s in statuses if s.enabled and not s.configured]
    if a_medias:
        # Solo el motivo, no las instrucciones: el `detail` de imgur o giphy
        # trae la URL donde sacar la clave y el limite del plan gratuito, que
        # esta muy bien en `/sources` y aqui convierte el resumen en un muro.
        detalle = "; ".join(f"{s.name} ({_motivo(s.detail)})" for s in a_medias)
        lineas.append(f"Por revisar ({len(a_medias)}): {html.escape(detalle)}")

    activos = len(list(iter_active()))
    detalle = "limpio" if activos == 0 else "hay descargas en curso"
    lineas.append(f"Workspaces activos: {activos} ({detalle})")

    return "\n".join(lineas)


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/status` — todo el estado de un vistazo.

    Existe porque saberlo obligaba a encadenar `/start`, `/health`, `/sources`
    y `/stats`, y desde el movil eso es cuatro mensajes para responder a «sigue
    vivo?». Los cuatro se quedan para el detalle; este es el resumen.
    """
    message = update.effective_message
    if message is None:
        return
    texto = await texto_status(_app(context), _scheduler(context))
    await message.reply_text(texto, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/stats [dias]` — publicaciones por fuente en una ventana de tiempo."""
    message = update.effective_message
    if message is None:
        return

    args = parse_args(context)
    if not args:
        await message.reply_text("De que periodo?", reply_markup=menu_stats())
        return

    days = int(args[0]) if args[0].isdigit() else 7
    # Cero es «todo», igual que el boton «Todo» del menu. Sin esto, `/stats 0`
    # preguntaba por los ultimos cero dias y respondia que no habia nada, que
    # es literalmente cierto y completamente inutil.
    since = None if days == 0 else utcnow() - timedelta(days=days)
    titulo = "Historico completo" if days == 0 else f"Ultimos {days} dias"

    app = _app(context)
    counts = await app.state.stats(since=since)
    total = await app.state.total_published()

    if not counts:
        await message.reply_text(f"{titulo}: nada publicado. Historico: {total} items.")
        return

    body = "\n".join(f"· {source}: {count}" for source, count in sorted(counts.items()))
    await message.reply_text(
        f"<b>{titulo}</b>\n{body}\n\nHistorico total: {total}",
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
    app = _app(context)
    report = await app.health()
    header = "Todo correcto" if report.ok else "Hay algo que revisar"
    await message.reply_text(
        f"<b>{header}</b>\n<pre>{html.escape(report.render())}</pre>\n"
        # Fuera del <pre>: lleva negrita y no es salida de diagnostico.
        f"{linea_scheduler(app, _scheduler(context))}",
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
def register_handlers(
    application: Application,  # type: ignore[type-arg]
    app: ScrappyApp,
    scheduler: SchedulerProtocol | None = None,
    *,
    recargador: Recargador | None = None,
) -> None:
    """Conecta los comandos y deja `ScrappyApp` accesible en `bot_data`.

    Args:
        scheduler: opcional, para poder responder «cuando es la proxima
            ronda». Sin el, el bot no tiene forma de saberlo: los ajustes
            dicen cada cuanto, pero la hora concreta solo la sabe quien tiene
            el job programado. `scrappy run --no-bot` no pasa por aqui, y en
            los tests no siempre hay uno.
        recargador: como pedir que Scrappy se reconstruya, para los cambios
            del `.env` que no se aplican de otra forma. Sin el, quien lo
            necesite responde que el cambio se aplicara al reiniciar.
    """
    application.bot_data["scrappy_app"] = app
    application.bot_data["admin_ids"] = app.settings.admin_ids
    application.bot_data["scheduler"] = scheduler
    application.bot_data[CLAVE_RECARGA] = recargador

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("fetch", cmd_fetch))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("sources", cmd_sources))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("config", cmd_config))
    application.add_handler(CommandHandler("health", cmd_health))
    application.add_handler(CommandHandler("purge", cmd_purge))
    application.add_handler(CallbackQueryHandler(on_callback))
    # El unico MessageHandler del bot. `filters.REPLY` es lo que impide que se
    # trague la conversacion entera: solo mira respuestas, y de esas solo actua
    # sobre las que contestan a una pregunta suya.
    application.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, on_respuesta)
    )
    application.add_error_handler(on_error)

    # Registra el menu nativo al arrancar: es lo que hace que Telegram
    # autocomplete al escribir «/» en vez de tener que recordar los comandos.
    application.post_init = _registrar_menu

    if not app.settings.admin_ids:
        log.warning(
            "no_admins_configured",
            detail="SCRAPPY_TELEGRAM_ADMIN_IDS esta vacio: nadie podra usar comandos",
        )


#: Lo que Telegram muestra al escribir «/». El orden es el de uso esperado, no
#: alfabetico: primero lo que se usa a diario.
_MENU = [
    ("status", "Como esta Scrappy"),
    ("start", "Comprobar que todo funciona"),
    ("fetch", "Buscar y publicar ahora"),
    ("sources", "Estado de cada fuente"),
    ("stats", "Que se ha publicado"),
    ("pause", "Parar el scheduler"),
    ("resume", "Reanudar el scheduler"),
    ("health", "Diagnostico del sistema"),
    ("config", "Configuracion efectiva"),
    ("purge", "Borrar los ficheros temporales"),
    ("help", "Lista de comandos"),
]


async def _registrar_menu(application: Application) -> None:  # type: ignore[type-arg]
    """Publica el menu de comandos. Un fallo aqui no debe impedir arrancar."""
    from telegram import BotCommand

    try:
        await application.bot.set_my_commands(
            [BotCommand(comando, descripcion) for comando, descripcion in _MENU]
        )
        log.info("bot_menu_registered", count=len(_MENU))
    except Exception as exc:  # el bot funciona igual sin menu
        log.warning("bot_menu_failed", error=str(exc))
