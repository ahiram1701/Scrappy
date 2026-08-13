"""Panel: estado del sistema, fuentes, estadisticas y control del scheduler.

Todo lo que muestra sale de `ScrappyApp.health()` y `source_statuses()`, que ya
alimentan `scrappy health` y el comando `/sources` del bot. Aqui solo se pintan.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from scrappy.core.models import utcnow
from scrappy.observability.logging import get_logger

if TYPE_CHECKING:
    from scrappy.tui.main import ScrappyTUI

log = get_logger(__name__)


class DashboardScreen(Screen[None]):
    """Vista de estado general."""

    @property
    def tui(self) -> ScrappyTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll():
            with Vertical(classes="seccion"):
                yield Static("Sistema", classes="seccion-titulo")
                yield Static(id="salud")
            with Vertical(classes="seccion"):
                yield Static("Fuentes", classes="seccion-titulo")
                yield DataTable(id="tabla-fuentes", cursor_type="row")
            with Vertical(classes="seccion"):
                yield Static("Scheduler", classes="seccion-titulo")
                yield Static(id="estado-scheduler")
                with Horizontal(id="acciones-scheduler"):
                    yield Button("Arrancar", id="arrancar", variant="success")
                    yield Button("Pausar", id="pausar", variant="warning")
                    yield Button("Reanudar", id="reanudar")
            with Vertical(classes="seccion"):
                yield Static("Publicado", classes="seccion-titulo")
                yield Static(id="stats")
            with Vertical(classes="seccion"):
                yield Static("Configuracion", classes="seccion-titulo")
                yield Static(id="estado-config")
                with Horizontal(id="acciones-config"):
                    yield Button("Recargar configuracion", id="recargar-config")
        yield Footer()

    async def on_mount(self) -> None:
        tabla = self.query_one("#tabla-fuentes", DataTable)
        tabla.add_columns("fuente", "activa", "configurada", "detalle")
        await self.refresh_data()

    # ------------------------------------------------------------------
    async def refresh_data(self) -> None:
        """Recarga salud, fuentes y estadisticas."""
        scrappy = self.tui.scrappy
        if scrappy is None:
            self.query_one("#salud", Static).update(
                "Scrappy no ha arrancado. Revisa la barra de estado."
            )
            return

        report = await scrappy.health()
        self.query_one("#salud", Static).update(
            "\n".join(
                [
                    f"ffmpeg          {'OK' if report.ffmpeg else 'NO ENCONTRADO'}",
                    f"estado          {report.state_backend} "
                    f"({report.state_items} items recordados)",
                    f"telegram        "
                    f"{'configurado' if report.telegram_configured else 'SIN configurar'}",
                    f"workspaces      {report.active_workspaces} "
                    f"({'limpio' if report.active_workspaces == 0 else 'descargas en curso'})",
                ]
            )
        )

        tabla = self.query_one("#tabla-fuentes", DataTable)
        tabla.clear()
        for estado in report.sources:
            tabla.add_row(
                estado.name,
                "si" if estado.enabled else "no",
                "si" if estado.configured else "no",
                estado.detail or "—",
            )

        await self._refresh_stats(scrappy)
        self._refresh_scheduler()
        self._refresh_config(scrappy)

    def _refresh_config(self, scrappy: object) -> None:
        settings = scrappy.settings  # type: ignore[attr-defined]
        self.query_one("#estado-config", Static).update(
            "\n".join(
                [
                    f"fichero         {self.tui.env_path}",
                    # La zona resuelta, no la escrita: si el `.env` la lleva
                    # vacia lo util es saber cual se detecto.
                    f"zona horaria    {settings.tzinfo}",
                    f"catalogo        {settings.sources_config_path}",
                ]
            )
        )

    async def _refresh_stats(self, scrappy: object) -> None:
        state = scrappy.state  # type: ignore[attr-defined]
        semana = await state.stats(since=utcnow() - timedelta(days=7))
        total = await state.total_published()

        if semana:
            detalle = "  ".join(f"{fuente}: {n}" for fuente, n in sorted(semana.items()))
        else:
            detalle = "nada en los ultimos 7 dias"
        self.query_one("#stats", Static).update(f"{detalle}\nhistorico: {total}")

    def _refresh_scheduler(self) -> None:
        scheduler = self.tui.scheduler
        scrappy = self.tui.scrappy
        if scheduler is None or scrappy is None:
            return

        proxima = scheduler.next_run_at
        if proxima is None:
            texto = "parado"
        elif scrappy.paused:
            texto = "en pausa (`/fetch` y el boton de publicar siguen funcionando)"
        else:
            texto = f"proxima ronda: {proxima}"
        self.query_one("#estado-scheduler", Static).update(texto)

    # ------------------------------------------------------------------
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Antes de exigir que Scrappy este montado: recargar es justamente lo
        # que puede arreglar un arranque fallido por configuracion.
        if event.button.id == "recargar-config":
            await self.tui.action_recargar()
            await self.refresh_data()
            return

        scrappy = self.tui.require_scrappy()
        scheduler = self.tui.scheduler
        if scrappy is None or scheduler is None:
            return

        match event.button.id:
            case "arrancar":
                if not self.tui.can_publish:
                    self.notify(
                        "Sin Telegram configurado el scheduler no puede publicar.",
                        severity="warning",
                    )
                    return
                scheduler.start()
                self.tui.set_status("Scheduler arrancado")
            case "pausar":
                scrappy.paused = True
                self.tui.set_status("Scheduler en pausa")
            case "reanudar":
                scrappy.paused = False
                self.tui.set_status("Scheduler reanudado")

        self._refresh_scheduler()
