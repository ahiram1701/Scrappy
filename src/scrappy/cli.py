"""Interfaz de linea de comandos.

    scrappy health                      diagnostico del sistema
    scrappy sources                     estado de cada fuente
    scrappy fetch --dry-run             ranking sin descargar ni publicar
    scrappy fetch --source reddit -n 2  publica 2 items de Reddit
    scrappy run                         bot + scheduler (lo que corre en Docker)
    scrappy whoami                      datos del bot y como obtener el chat id

`fetch --dry-run` es el comando con el que se calibra el proyecto: recorre
descubrimiento, filtros y ranking, imprime la tabla con el desglose de cada
score y no toca ni el disco ni Telegram.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from scrappy import __version__
from scrappy.app import ScrappyApp, load_settings_or_die
from scrappy.bot.handlers import register_handlers
from scrappy.core.errors import ScrappyError
from scrappy.observability.logging import configure_logging
from scrappy.scheduler.jobs import PipelineScheduler

console = Console()
error_console = Console(stderr=True)

cli = typer.Typer(
    name="scrappy",
    help="Curador de videos cortos y memes hacia Telegram, sin dejar rastro en disco.",
    no_args_is_help=True,
    add_completion=False,
)


def _fail(message: str) -> None:
    error_console.print(f"[bold red]Error:[/] {message}")
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
                console.print("[green]Scheduler en marcha.[/] Ctrl+C para parar.")
                await asyncio.Event().wait()  # espera indefinida
                return 0

            from telegram.ext import ApplicationBuilder

            application = (
                ApplicationBuilder().token(settings.telegram_bot_token.get_secret_value()).build()
            )
            register_handlers(application, app)

            async with application:
                await application.start()
                if application.updater is not None:
                    await application.updater.start_polling(drop_pending_updates=True)
                console.print("[green]Bot y scheduler en marcha.[/] Ctrl+C para parar.")
                await asyncio.Event().wait()
            return 0

        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\nParando…")
            return 0
        finally:
            scheduler.shutdown()
            await app.aclose()

    raise typer.Exit(code=_execute(_run))


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
def _execute(coro_factory: object) -> int:
    """Ejecuta una corrutina traduciendo los errores del proyecto a codigos de salida."""
    try:
        return int(asyncio.run(coro_factory()))  # type: ignore[operator]
    except ScrappyError as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


def main() -> None:
    """Punto de entrada del script `scrappy`."""
    try:
        cli()
    except ScrappyError as exc:  # pragma: no cover - red de seguridad
        error_console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
