"""Configuracion: editar `sources.yaml` sin salir de la TUI.

Expone un subconjunto curado: los pesos del ranking, `min_score`, y por cada
fuente su `weight`, `budget` y sus listas (subreddits, comunidades, consultas).

Lo que **no** hace es crear ni borrar secciones. El editor solo cambia valores
de claves existentes, porque generar estructura desde una interfaz es donde mas
dano haria y el fichero de ejemplo ya trae todo. Ver `tui/yaml_editor.py`.

El guardado valida antes de escribir: si algo esta mal, te quedas con lo que
tenias en vez de con un `sources.yaml` roto que impida arrancar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger
from scrappy.tui.yaml_editor import SourcesYamlEditor

if TYPE_CHECKING:
    from scrappy.tui.main import ScrappyTUI

log = get_logger(__name__)

#: Claves de lista que tiene cada fuente. Se editan como texto separado por
#: comas, que para listas de nombres cortos es mas comodo que un widget de
#: lista con altas y bajas.
_LISTAS_POR_FUENTE = {
    "reddit": ["subreddits"],
    "lemmy": ["communities"],
    "bluesky": ["queries"],
    "imgur": ["tags"],
    "giphy": ["queries"],
    "youtube": ["queries", "channels"],
    "x": ["queries", "accounts"],
    "tiktok": ["hashtags", "accounts"],
    "instagram": ["hashtags", "accounts"],
}

#: Campos numericos comunes a todas las fuentes.
_NUMEROS_POR_FUENTE = ["weight", "budget"]


def _campo(etiqueta: str, widget_id: str, valor: str) -> Horizontal:
    return Horizontal(
        Static(etiqueta, classes="campo-etiqueta"),
        Input(value=valor, id=widget_id),
        classes="campo",
    )


class SettingsScreen(Screen[None]):
    """Editor de `config/sources.yaml`."""

    BINDINGS: ClassVar[list[BindingType]] = [("ctrl+s", "guardar", "Guardar")]

    def __init__(self) -> None:
        super().__init__()
        self._editor: SourcesYamlEditor | None = None

    @property
    def tui(self) -> ScrappyTUI:
        return self.app  # type: ignore[return-value]

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="config-aviso", classes="ayuda")
        with Horizontal(id="acciones-scheduler"):
            yield Button("Guardar", id="guardar", variant="success")
            yield Button("Descartar cambios", id="recargar")
        yield VerticalScroll(id="config-scroll")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        """Relee el fichero y reconstruye el formulario."""
        scrappy = self.tui.scrappy
        if scrappy is None:
            return

        editor = SourcesYamlEditor(scrappy.settings.sources_config_path)
        try:
            editor.load()
        except ConfigError as exc:
            self.query_one("#config-aviso", Static).update(str(exc))
            return

        self._editor = editor
        await self._construir_formulario(editor)
        self.query_one("#config-aviso", Static).update(
            f"Editando {editor.path}. Los comentarios del fichero se conservan al guardar."
        )

    async def _construir_formulario(self, editor: SourcesYamlEditor) -> None:
        contenedor = self.query_one("#config-scroll", VerticalScroll)
        await contenedor.remove_children()

        # --- ranking ---
        pesos = Vertical(classes="seccion")
        await contenedor.mount(pesos)
        await pesos.mount(Static("Ranking", classes="seccion-titulo"))
        await pesos.mount(
            Static(
                "Los tres pesos deberian sumar 1.0. min_score es la nota minima "
                "para que un item se llegue a descargar.",
                classes="ayuda",
            )
        )
        for clave in ("engagement", "velocity", "source"):
            await pesos.mount(
                _campo(
                    f"peso {clave}",
                    f"rk-{clave}",
                    str(editor.get_value(["ranking", "weights", clave], "")),
                )
            )
        await pesos.mount(
            _campo("min_score", "rk-min_score", str(editor.get_value(["ranking", "min_score"], "")))
        )

        # --- fuentes ---
        for fuente in editor.source_names():
            bloque = Vertical(classes="seccion")
            await contenedor.mount(bloque)
            await bloque.mount(Static(fuente, classes="seccion-titulo"))

            for clave in _NUMEROS_POR_FUENTE:
                valor = editor.get_value(["sources", fuente, clave])
                if valor is None:
                    continue
                await bloque.mount(_campo(clave, f"src-{fuente}-{clave}", str(valor)))

            for clave in _LISTAS_POR_FUENTE.get(fuente, []):
                valor = editor.get_value(["sources", fuente, clave])
                if valor is None:
                    continue
                texto = ", ".join(str(item) for item in valor)
                await bloque.mount(_campo(clave, f"src-{fuente}-{clave}", texto))

    # ------------------------------------------------------------------
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guardar":
            await self.action_guardar()
        elif event.button.id == "recargar":
            await self.refresh_data()
            self.tui.set_status("Cambios descartados")

    async def action_guardar(self) -> None:
        editor = self._editor
        scrappy = self.tui.scrappy
        if editor is None or scrappy is None:
            return

        try:
            self._volcar_formulario(editor)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return

        try:
            config = editor.save()
        except ConfigError as exc:
            # El fichero no se ha tocado: `save()` valida antes de escribir.
            self.notify(str(exc), severity="error")
            self.query_one("#config-aviso", Static).update(
                "No se guardo nada. El fichero sigue como estaba."
            )
            return

        # Recargar en caliente: el pipeline usa esta config en el proximo run.
        scrappy.sources_config = config
        self.tui.set_status(f"Guardado en {editor.path}")
        self.notify("Configuracion guardada. Se aplica en la proxima ronda.")

    def _volcar_formulario(self, editor: SourcesYamlEditor) -> None:
        """Lleva lo escrito en los campos al editor.

        Raises:
            ValueError: algun campo numerico no lo es.
        """
        for campo in self.query(Input):
            identificador = campo.id or ""
            texto = campo.value.strip()

            if identificador.startswith("rk-"):
                clave = identificador.removeprefix("rk-")
                ruta = (
                    ["ranking", "min_score"]
                    if clave == "min_score"
                    else ["ranking", "weights", clave]
                )
                editor.set_value(ruta, _como_numero(texto, clave))
                continue

            if identificador.startswith("src-"):
                _, fuente, clave = identificador.split("-", 2)
                ruta = ["sources", fuente, clave]
                if clave in _NUMEROS_POR_FUENTE:
                    editor.set_value(ruta, _como_numero(texto, f"{fuente}.{clave}"))
                else:
                    partes = [p.strip() for p in texto.split(",") if p.strip()]
                    editor.set_value(ruta, partes)


def _como_numero(texto: str, etiqueta: str) -> float | int:
    """Convierte a int si no tiene decimales, para no escribir `budget: 60.0`."""
    try:
        valor = float(texto)
    except ValueError as exc:
        raise ValueError(f"«{etiqueta}» debe ser un numero, y pone «{texto}»") from exc
    return int(valor) if valor.is_integer() else valor
