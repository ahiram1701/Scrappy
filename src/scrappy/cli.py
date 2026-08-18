"""Interfaz de linea de comandos.

    scrappy health                      diagnostico del sistema
    scrappy sources                     estado de cada fuente
    scrappy fetch --dry-run             ranking sin descargar ni publicar
    scrappy fetch --source reddit -n 2  publica 2 items de Reddit
    scrappy run                         bot + scheduler (lo que corre en Docker)
    scrappy autostart --sistema         que arranque al encender, sin iniciar sesion
    scrappy whoami                      datos del bot y como obtener el chat id

`fetch --dry-run` es el comando con el que se calibra el proyecto: recorre
descubrimiento, filtros y ranking, imprime la tabla con el desglose de cada
score y no toca ni el disco ni Telegram.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from scrappy import __version__
from scrappy.app import ScrappyApp, load_settings_or_die
from scrappy.bot.avisos import avisar_arranque
from scrappy.bot.listener import BotListener
from scrappy.core.errors import ScrappyError
from scrappy.observability.logging import configure_logging, get_logger
from scrappy.scheduler.jobs import PipelineScheduler

console = Console()
error_console = Console(stderr=True)

log = get_logger(__name__)

cli = typer.Typer(
    name="scrappy",
    help="Curador de videos cortos y memes hacia Telegram, sin dejar rastro en disco.",
    no_args_is_help=True,
    add_completion=False,
)


def _mostrar_error(mensaje: str) -> None:
    """Ensena un error por donde se pueda leerlo.

    Sin consola -la tarea de autoarranque corre con `pythonw`- `error_console`
    escribe a la nada, y un fallo de arranque seria un proceso que muere sin
    dejar rastro en ningun sitio. Ahi el error va tambien al log en fichero.
    """
    error_console.print(f"[bold red]Error:[/] {mensaje}")
    if sys.stdout is None:
        log.error("cli_error", detalle=mensaje)


def _fail(message: str) -> None:
    _mostrar_error(message)
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@cli.command()
def health() -> None:
    """Comprueba ffmpeg, el backend de estado, Telegram y las fuentes."""

    async def _run() -> int:
        settings = load_settings_or_die()
        async with await ScrappyApp.create(settings, with_publisher=False) as app:
            report = await app.health()
            console.print(report.render())
            if report.active_workspaces:
                console.print(
                    "\n[yellow]Hay workspaces activos. Si no hay ninguna descarga en "
                    "curso, ejecuta `scrappy purge`.[/]"
                )
            return 0 if report.ok else 1

    raise typer.Exit(code=_execute(_run))


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
@cli.command()
def sources() -> None:
    """Muestra el estado de cada fuente."""

    async def _run() -> int:
        settings = load_settings_or_die()
        async with await ScrappyApp.create(settings, with_publisher=False) as app:
            table = Table(title="Fuentes")
            table.add_column("fuente")
            table.add_column("activa")
            table.add_column("configurada")
            table.add_column("detalle", overflow="fold")

            for status in await app.source_statuses():
                table.add_row(
                    status.name,
                    "si" if status.enabled else "no",
                    "[green]si[/]" if status.configured else "[red]no[/]",
                    status.detail,
                )
            console.print(table)
        return 0

    raise typer.Exit(code=_execute(_run))


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
@cli.command()
def fetch(
    source: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Limita el run a una sola fuente."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Cuantos items publicar como maximo."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="No descarga ni publica: solo rankea y explica."),
    ] = False,
) -> None:
    """Ejecuta el pipeline una vez."""

    async def _run() -> int:
        settings = load_settings_or_die()
        async with await ScrappyApp.create(
            settings, only_source=source, with_publisher=not dry_run
        ) as app:
            report = await app.run_pipeline(limit=limit, dry_run=dry_run)

            if dry_run:
                _print_dry_run(app)
            console.print(f"\n[bold]{report.summary_line()}[/]")

            for error in report.errors:
                console.print(f"[yellow]· {error}[/]")
            return 0

    raise typer.Exit(code=_execute(_run))


def _print_dry_run(app: ScrappyApp) -> None:
    """Tabla con el desglose de cada score, para calibrar los pesos."""
    rows = app.pipeline.last_dry_run
    if not rows:
        console.print("[yellow]Ningun candidato llego al ranking.[/]")
        return

    table = Table(title="Candidatos evaluados (no se descargo nada)")
    table.add_column("score", justify="right")
    table.add_column("fuente")
    table.add_column("veredicto")
    table.add_column("engagement", justify="right")
    table.add_column("titulo", overflow="ellipsis", max_width=48)

    for row in rows:
        style = "green" if row.verdict == "SELECCIONADO" else "dim"
        table.add_row(
            f"{row.score:.3f}",
            row.candidate.source,
            f"[{style}]{row.verdict}[/]",
            f"{row.candidate.engagement:,}",
            row.candidate.title or "(sin titulo)",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
@cli.command()
def run(
    no_bot: Annotated[
        bool,
        typer.Option("--no-bot", help="Solo scheduler, sin escuchar comandos."),
    ] = False,
) -> None:
    """Arranca el bot y el scheduler. Es lo que ejecuta el contenedor."""

    async def _run() -> int:
        settings = load_settings_or_die()
        app = await ScrappyApp.create(settings)
        scheduler = PipelineScheduler(app)

        try:
            scheduler.start()

            if no_bot:
                await avisar_arranque(app, scheduler, escuchando=False)
                console.print("[green]Scheduler en marcha.[/] Ctrl+C para parar.")
                await asyncio.Event().wait()  # espera indefinida
                return 0

            # El mismo BotListener que usa la TUI. Tenerlo escrito dos veces
            # fue lo que dejo a la interfaz publicando sin escuchar.
            listener = BotListener(app, scheduler)
            if not await _escuchar_con_reintentos(listener):
                _fail("No se pudo conectar con Telegram. Ejecuta `scrappy doctor`.")
                return 1

            try:
                # Despues de arrancar del todo, para que el aviso pueda decir
                # la verdad sobre si escucha comandos y cuando es la ronda.
                await avisar_arranque(app, scheduler)
                console.print("[green]Bot y scheduler en marcha.[/] Ctrl+C para parar.")
                await asyncio.Event().wait()
            finally:
                await listener.stop()
            return 0

        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\nParando…")
            return 0
        finally:
            scheduler.shutdown()
            await app.aclose()

    raise typer.Exit(code=_execute(_run))


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
@cli.command()
def doctor(
    offline: Annotated[
        bool,
        typer.Option("--offline", help="No consultar a Telegram; solo revisar el formato."),
    ] = False,
) -> None:
    """Revisa la configuracion y explica como arreglar lo que falle."""

    async def _run() -> int:
        from scrappy.diagnostics import CheckStatus, run_diagnostics

        settings = load_settings_or_die()
        diagnosis = await run_diagnostics(settings, use_network=not offline)

        estilos = {
            CheckStatus.OK: ("[green]OK[/]", ""),
            CheckStatus.WARNING: ("[yellow]AVISO[/]", "yellow"),
            CheckStatus.ERROR: ("[red]FALLO[/]", "red"),
            CheckStatus.SKIPPED: ("[dim]--[/]", "dim"),
        }

        table = Table(title="Diagnostico de Scrappy", show_lines=False)
        table.add_column("", width=7)
        table.add_column("comprobacion", style="bold")
        table.add_column("detalle", overflow="fold")

        for check in diagnosis.checks:
            marca, estilo = estilos[check.status]
            detalle = check.detail
            if check.fix and check.status is not CheckStatus.OK:
                detalle += f"\n[dim]-> {check.fix}[/]"
            table.add_row(marca, f"[{estilo}]{check.name}[/]" if estilo else check.name, detalle)

        console.print(table)
        console.print(f"\n[bold]{diagnosis.summary()}[/]")

        if diagnosis.blocking:
            console.print(
                "\nArregla lo marcado como [red]FALLO[/] y vuelve a ejecutar "
                "[bold]scrappy doctor[/]."
            )
            return 1
        if diagnosis.warnings:
            console.print("\nPuedes usar Scrappy, pero revisa los avisos.")
        return 0

    raise typer.Exit(code=_execute(_run))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
@cli.command()
def init() -> None:
    """Crea el fichero .env preguntando paso a paso y validando cada valor."""

    async def _run() -> int:
        from scrappy.tui.env_editor import EnvEditor

        destino = Path(".env")
        plantilla = Path(".env.example")

        if not plantilla.exists():
            _fail(f"No se encuentra {plantilla}. Ejecuta esto desde la carpeta del proyecto.")

        if destino.exists():
            console.print(f"[yellow]{destino} ya existe.[/]")
            if not typer.confirm("Se sobrescribiran los valores que cambies. Continuar?"):
                console.print("Cancelado. No se ha tocado nada.")
                return 0

        editor = EnvEditor(destino if destino.exists() else plantilla)
        editor.load()
        editor.path = destino

        console.print("\n[bold]Configuracion de Telegram[/]")
        console.print(
            "[dim]El token te lo da @BotFather con /newbot. No se mostrara al escribirlo.[/]\n"
        )

        token = await _pedir_token()
        if token is None:
            return 1
        editor.set_value("SCRAPPY_TELEGRAM_BOT_TOKEN", token)

        chat_id = await _pedir_chat(token)
        if chat_id is None:
            return 1
        editor.set_value("SCRAPPY_TELEGRAM_TARGET_CHAT_ID", chat_id)

        console.print(
            "\n[dim]Tu id de usuario, para poder usar los comandos del bot. "
            "Te lo dice @userinfobot.[/]"
        )
        admin = typer.prompt("Tu id de Telegram", default=chat_id if chat_id.isdigit() else "")
        editor.set_value("SCRAPPY_TELEGRAM_ADMIN_IDS", admin.strip())

        console.print("\n[bold]Reddit[/]")
        console.print("[dim]No necesita credenciales, solo que te identifiques.[/]")
        usuario = typer.prompt("Tu usuario de Reddit (sin /u/)", default="")
        if usuario.strip():
            editor.set_value(
                "SCRAPPY_REDDIT_USER_AGENT",
                f"windows:scrappy:0.1.0 (by /u/{usuario.strip()})",
            )

        editor.save()
        console.print(f"\n[green]Escrito {destino}[/]")
        console.print("Comprueba que todo esta bien con: [bold]scrappy doctor[/]")
        return 0

    raise typer.Exit(code=_execute(_run))


async def _pedir_token() -> str | None:
    """Pide el token hasta que tenga formato valido y Telegram lo acepte."""
    from telegram import Bot
    from telegram.error import TelegramError

    from scrappy.diagnostics import CheckStatus, check_token_format

    for intento in range(3):
        token = str(typer.prompt("Token del bot", hide_input=True)).strip()

        formato = check_token_format(token)
        if formato.status is CheckStatus.ERROR:
            console.print(f"[red]{formato.detail}[/]\n[dim]{formato.fix}[/]")
            continue

        try:
            bot = Bot(token)
            async with bot:
                me = await bot.get_me()
            console.print(f"[green]Conectado como @{me.username}[/]")
            return token
        except TelegramError as exc:
            console.print(f"[red]Telegram rechazo el token:[/] {exc}")
            if intento < 2:
                console.print("[dim]Comprueba que lo copiaste entero y sin espacios.[/]")

    error_console.print("[red]No se pudo validar el token tras 3 intentos.[/]")
    return None


async def _pedir_chat(token: str) -> str | None:
    """Pide el chat destino y comprueba que el bot puede alcanzarlo."""
    from telegram import Bot
    from telegram.error import TelegramError

    from scrappy.diagnostics import CheckStatus, check_chat_id_format

    console.print(
        "\n[dim]Para recibirlo en privado, pon tu id de usuario (POSITIVO) y "
        "pulsa Start en tu bot.\nPara un canal, su id (empieza por -100) con el "
        "bot como administrador.[/]"
    )

    for _ in range(3):
        chat_id = str(typer.prompt("Chat destino")).strip()

        formato = check_chat_id_format(chat_id)
        if formato.status is CheckStatus.ERROR:
            console.print(f"[red]{formato.detail}[/]\n[dim]{formato.fix}[/]")
            continue
        if formato.status is CheckStatus.WARNING:
            console.print(f"[yellow]{formato.detail}[/]\n[dim]{formato.fix}[/]")
            if not typer.confirm("Usarlo de todas formas?", default=False):
                continue

        try:
            bot = Bot(token)
            async with bot:
                chat = await bot.get_chat(chat_id)
            console.print(f"[green]El bot alcanza «{chat.title or chat.full_name}»[/]")
            return chat_id
        except TelegramError as exc:
            console.print(f"[red]No se puede acceder a ese chat:[/] {exc}")
            if chat_id.isdigit():
                console.print("[dim]Abre tu bot en Telegram y pulsa Start.[/]")
            else:
                console.print("[dim]Anade el bot al canal como administrador.[/]")

    error_console.print("[red]No se pudo validar el chat tras 3 intentos.[/]")
    return None


# ---------------------------------------------------------------------------
# tui
# ---------------------------------------------------------------------------
@cli.command()
def tui() -> None:
    """Abre la interfaz de terminal. Es lo que lanza `Scrappy.bat`."""
    # Import tardio: Textual tarda un poco en cargar y el resto de comandos no
    # lo necesitan.
    from scrappy.tui import run_tui

    run_tui()


# ---------------------------------------------------------------------------
# autostart
# ---------------------------------------------------------------------------
@cli.command()
def autostart(
    sistema: Annotated[
        bool,
        typer.Option(
            "--sistema",
            help="Arranca al encender el equipo, sin iniciar sesion. Pide permiso (UAC).",
        ),
    ] = False,
    sesion: Annotated[
        bool,
        typer.Option("--sesion", help="Arranca al iniciar sesion."),
    ] = False,
    quitar: Annotated[
        bool,
        typer.Option("--quitar", help="Deja de arrancar solo."),
    ] = False,
) -> None:
    """Consulta o cambia el arranque automatico. Sin opciones, solo informa.

    El directorio de trabajo que se registra es este, porque de aqui se leen
    `.env` y `config/`: ejecutalo desde la carpeta de Scrappy.
    """
    from scrappy.autostart import Autoarranque

    if sum((sistema, sesion, quitar)) > 1:
        _fail("Elige solo una: --sistema, --sesion o --quitar.")

    autoarranque = Autoarranque()

    if sistema:
        console.print("Windows va a pedir permiso de administrador…")
        estado = autoarranque.enable("sistema")
    elif sesion:
        estado = autoarranque.enable()
    elif quitar:
        estado = autoarranque.disable()
    else:
        estado = autoarranque.status()

    color = "green" if estado.activo else "yellow"
    console.print(f"[{color}]{estado.detalle}[/]")
    raise typer.Exit(code=0 if estado.activo or quitar or not (sistema or sesion) else 1)


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------
@cli.command()
def purge() -> None:
    """Borra cualquier workspace temporal que hubiera quedado."""
    from scrappy.download.workspace import purge_active, sweep_orphans

    settings = load_settings_or_die()
    configure_logging(settings.log_level, settings.log_format)
    removed = purge_active() + sweep_orphans(settings.workspace_root)
    console.print(f"Workspaces borrados: {removed}. El disco queda limpio.")


# ---------------------------------------------------------------------------
# whoami
# ---------------------------------------------------------------------------
@cli.command()
def whoami() -> None:
    """Muestra la identidad del bot y comprueba el acceso al chat destino."""

    async def _run() -> int:
        settings = load_settings_or_die()
        settings.validate_for_publishing()

        from telegram import Bot

        bot = Bot(settings.telegram_bot_token.get_secret_value())
        async with bot:
            me = await bot.get_me()
            console.print(f"Bot: @{me.username} (id {me.id})")
            try:
                chat = await bot.get_chat(settings.telegram_target_chat_id)
                console.print(
                    f"Chat destino: {chat.title or chat.full_name or chat.id} "
                    f"(id {chat.id}, tipo {chat.type})"
                )
            except Exception as exc:  # cualquier fallo aqui es informativo
                console.print(f"[red]No se pudo acceder al chat destino:[/] {exc}")
                console.print(
                    "Anade el bot al canal como administrador y comprueba "
                    "SCRAPPY_TELEGRAM_TARGET_CHAT_ID."
                )
                return 1
        return 0

    raise typer.Exit(code=_execute(_run))


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
@cli.command()
def version() -> None:
    """Muestra la version instalada."""
    console.print(f"scrappy {__version__}")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
#: Cuantas veces se intenta conectar con Telegram al arrancar, y cuanto se
#: espera entre intentos. Dos minutos y medio en total: de sobra para que un
#: portatil recien encendido termine de asociarse al wifi.
_INTENTOS_CONEXION = 6
_ESPERA_ENTRE_INTENTOS = 30.0


async def _escuchar_con_reintentos(listener: BotListener) -> bool:
    """Arranca el listener insistiendo un rato antes de rendirse.

    Existe por el autoarranque: al iniciar sesion el proceso se lanza antes de
    que la red este lista, y rendirse al primer intento dejaba a Scrappy sin
    escuchar hasta el siguiente inicio de sesion. Insistir cuesta unos minutos
    de nada y ahorra un dia entero de bot mudo.
    """
    for intento in range(1, _INTENTOS_CONEXION + 1):
        if await listener.start():
            return True

        if intento < _INTENTOS_CONEXION:
            log.warning(
                "bot_listener_reintento",
                intento=intento,
                de=_INTENTOS_CONEXION,
                espera_segundos=_ESPERA_ENTRE_INTENTOS,
            )
            console.print(
                f"[yellow]Sin conexion con Telegram (intento {intento} de "
                f"{_INTENTOS_CONEXION}). Reintento en "
                f"{_ESPERA_ENTRE_INTENTOS:.0f}s…[/]"
            )
            await asyncio.sleep(_ESPERA_ENTRE_INTENTOS)

    return False


def _execute(coro_factory: object) -> int:
    """Ejecuta una corrutina traduciendo los errores del proyecto a codigos de salida."""
    try:
        return int(asyncio.run(coro_factory()))  # type: ignore[operator]
    except ScrappyError as exc:
        _mostrar_error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


def main() -> None:
    """Punto de entrada del script `scrappy`."""
    if sys.stdout is None:
        # Arrancado sin consola: la tarea de autoarranque lanza `pythonw -m
        # scrappy.cli run`. `ScrappyApp.create` ya configura el logging, pero
        # lo que falle antes -un `.env` mal escrito, sin ir mas lejos- moriria
        # sin dejar una sola linea en ningun sitio. Esto lo adelanta.
        configure_logging()

    try:
        cli()
    except ScrappyError as exc:  # pragma: no cover - red de seguridad
        _mostrar_error(str(exc))
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
