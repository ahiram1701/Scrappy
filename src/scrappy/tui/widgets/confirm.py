"""Modal de confirmacion para acciones que salen de la maquina.

Publicar en Telegram es irreversible desde la TUI: el mensaje llega a un canal
que puede tener gente leyendo. Por eso no basta con un boton; hace falta decir
antes **que** se va a publicar y **a donde**, y que la opcion por defecto sea
cancelar.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    """Pregunta si continuar. Devuelve True solo si se confirma explicitamente."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancelar", "Cancelar")]

    def __init__(self, titulo: str, detalle: str, confirmar: str = "Publicar") -> None:
        super().__init__()
        self._titulo = titulo
        self._detalle = detalle
        self._confirmar = confirmar

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-caja"):
            yield Static(self._titulo, id="confirm-titulo")
            yield Static(self._detalle, id="confirm-detalle")
            with Horizontal(id="confirm-botones"):
                yield Button("Cancelar", variant="default", id="cancelar")
                yield Button(self._confirmar, variant="warning", id="confirmar")

    def on_mount(self) -> None:
        # El foco arranca en Cancelar: pulsar Enter sin leer no debe publicar.
        self.query_one("#cancelar", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirmar")

    def action_cancelar(self) -> None:
        self.dismiss(False)
