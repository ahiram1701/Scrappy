"""Candidatos: el `--dry-run` de la CLI, pero navegable.

Ejecuta el pipeline en seco, llena una tabla con lo que publicaria y, al
seleccionar una fila, explica **por que** ha sacado esa nota. Esa explicacion es
lo que hace util la pantalla: calibrar los pesos a ciegas es adivinar.

Desde aqui tambien se publica, con una confirmacion que dice que se va a enviar
y a que chat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from scrappy.core.pipeline import DryRunRow, ProgressEvent
from scrappy.observability.logging import get_logger
from scrappy.tui.widgets.confirm import ConfirmModal

if TYPE_CHECKING:
    from scrappy.tui.main import ScrappyTUI

log = get_logger(__name__)

_SIN_SELECCION = "Selecciona una fila para ver por que ha sacado esa nota."


class CandidatesScreen(Screen[None]):
    """Explorador de candidatos con desglose del score."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("e", "explorar", "Explorar"),
        ("p", "publicar", "Publicar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._filas: list[DryRunRow] = []
        self._ocupado = False

    @property
    def tui(self) -> ScrappyTUI:
        return self.app  # type: ignore[return-value]

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="acciones-scheduler"):
            yield Button("Explorar (dry-run)", id="explorar", variant="primary")
            yield Button("Publicar", id="publicar", variant="warning")
        yield Static("", id="barra-progreso")
        with Horizontal(id="candidatos-layout"):
            yield DataTable(id="tabla-candidatos", cursor_type="row")
            with Vertical(id="desglose"):
                yield Static("Desglose", classes="seccion-titulo")
                yield Static(_SIN_SELECCION, id="desglose-texto")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#tabla-candidatos", DataTable)
        tabla.add_columns("score", "fuente", "veredicto", "engagement", "titulo")

        if not self.tui.can_publish:
            self.query_one("#publicar", Button).disabled = True

        self.query_one("#barra-progreso", Static).update(
            "Pulsa «Explorar» para ver que publicaria ahora mismo. No descarga nada."
        )

    async def refresh_data(self) -> None:
        """La tecla global `r` relanza la exploracion."""
        self.action_explorar()

    # ------------------------------------------------------------------
    # Exploracion
    # ------------------------------------------------------------------
    def action_explorar(self) -> None:
        if self._ocupado:
            self.notify("Ya hay una ejecucion en marcha.")
            return
        self._explorar()

    @work(exclusive=True)
    async def _explorar(self) -> None:
        """Dry-run en un worker: el pipeline tarda ~15s y la UI no puede colgarse."""
        scrappy = self.tui.require_scrappy()
        if scrappy is None:
            return

        self._ocupado = True
        progreso = self.query_one("#barra-progreso", Static)
        try:
            report = await scrappy.run_pipeline(dry_run=True, on_progress=self._on_progress)
        except Exception as exc:
            log.exception("dry_run_failed", error=str(exc))
            progreso.update(f"Fallo la exploracion: {exc}")
            self.notify(str(exc), severity="error")
            return
        finally:
            self._ocupado = False

        self._filas = list(scrappy.pipeline.last_dry_run)
        self._llenar_tabla()
        progreso.update(report.summary_line())
        self.tui.set_status(report.summary_line())

    def _on_progress(self, evento: ProgressEvent) -> None:
        """Lo llama el pipeline en cada etapa, desde el mismo bucle de eventos."""
        texto = evento.detail
        if evento.current is not None and evento.total is not None:
            texto = f"[{evento.current}/{evento.total}] {texto}"
        self.query_one("#barra-progreso", Static).update(texto)

    def _llenar_tabla(self) -> None:
        tabla = self.query_one("#tabla-candidatos", DataTable)
        tabla.clear()
        for fila in self._filas:
            tabla.add_row(
                f"{fila.score:.3f}",
                fila.candidate.source,
                fila.verdict,
                f"{fila.candidate.engagement:,}",
                fila.candidate.title[:60] or "(sin titulo)",
            )
        if not self._filas:
            self.query_one("#desglose-texto", Static).update("Ningun candidato llego al ranking.")

    # ------------------------------------------------------------------
    # Desglose
    # ------------------------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not self._filas or event.cursor_row >= len(self._filas):
            return
        self.query_one("#desglose-texto", Static).update(
            _formatear_desglose(self._filas[event.cursor_row])
        )

    # ------------------------------------------------------------------
    # Publicacion
    # ------------------------------------------------------------------
    def action_publicar(self) -> None:
        if not self.tui.can_publish:
            self.notify(
                "Sin Telegram configurado no se puede publicar. Revisa tu .env.",
                severity="warning",
            )
            return
        if self._ocupado:
            self.notify("Ya hay una ejecucion en marcha.")
            return
        self._publicar()

    @work(exclusive=True)
    async def _publicar(self) -> None:
        """Publica de verdad, previa confirmacion explicita."""
        scrappy = self.tui.require_scrappy()
        if scrappy is None:
            return

        cuantos = scrappy.settings.items_per_run
        destino = scrappy.settings.telegram_target_chat_id

        seleccionados = [f for f in self._filas if f.verdict == "SELECCIONADO"]
        if seleccionados:
            muestra = "\n".join(
                f"  · {f.candidate.source}  {f.score:.3f}  "
                f"{f.candidate.title[:44] or '(sin titulo)'}"
                for f in seleccionados[:cuantos]
            )
            detalle = (
                f"Se publicaran hasta {cuantos} items en el chat {destino}.\n\n"
                f"Ahora mismo encabezan la lista:\n{muestra}\n\n"
                "El pipeline se vuelve a ejecutar al confirmar, asi que la "
                "seleccion final puede variar."
            )
        else:
            detalle = (
                f"Se ejecutara el pipeline y se publicaran hasta {cuantos} items "
                f"en el chat {destino}.\n\n"
                "No has explorado antes, asi que no se puede mostrar la seleccion."
            )

        confirmado = await self.app.push_screen_wait(ConfirmModal("Publicar en Telegram", detalle))
        if not confirmado:
            self.tui.set_status("Publicacion cancelada")
            return

        self._ocupado = True
        progreso = self.query_one("#barra-progreso", Static)
        try:
            report = await scrappy.run_pipeline(on_progress=self._on_progress)
        except Exception as exc:
            log.exception("publish_failed", error=str(exc))
            progreso.update(f"Fallo la publicacion: {exc}")
            self.notify(str(exc), severity="error")
            return
        finally:
            self._ocupado = False

        progreso.update(report.summary_line())
        self.tui.set_status(report.summary_line())
        self.notify(f"Publicados {len(report.published)} items.")


def _formatear_desglose(fila: DryRunRow) -> str:
    """Explica de donde sale el score de un candidato.

    Es la parte que convierte la tabla en una herramienta de calibracion en vez
    de en una lista opaca. Ver `docs/RANKING.md`.
    """
    candidato = fila.candidate
    lineas = [
        f"{candidato.title[:70] or '(sin titulo)'}",
        "",
        f"fuente        {candidato.source}",
        f"autor         {candidato.author}",
        f"engagement    {candidato.engagement:,}",
        f"comentarios   {candidato.comments:,}",
        f"antiguedad    {candidato.age_hours():.0f}h",
        f"tipo          {candidato.kind}",
        "",
        f"veredicto     {fila.verdict}",
        f"score         {fila.score:.3f}",
        "",
        candidato.permalink,
    ]
    return "\n".join(lineas)
