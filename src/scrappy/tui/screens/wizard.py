"""Asistente de primera vez.

Aparece solo cuando el diagnostico encuentra algo que impide funcionar, y no
como un paso obligatorio: quien ya lo tiene configurado no deberia toparse con
un asistente cada vez que abre la aplicacion.

No duplica las comprobaciones: consume `diagnostics.py`, el mismo modulo que
usan `scrappy doctor` y el `/start` del bot.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from scrappy.diagnostics import CheckStatus, Diagnosis

#: Que devuelve el asistente al cerrarse.
#:   "settings"  -> el usuario quiere ir a arreglarlo
#:   None        -> lo deja para luego
WizardResult = str | None


class WizardScreen(ModalScreen[WizardResult]):
    """Explica que falta para que Scrappy funcione, y lleva a arreglarlo."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "luego", "Ahora no")]

    def __init__(self, diagnosis: Diagnosis) -> None:
        super().__init__()
        self._diagnosis = diagnosis

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wizard-caja"):
            yield Static("Falta configurar algo", id="wizard-titulo")
            yield Static(self._resumen(), id="wizard-texto")
            with Horizontal(id="wizard-botones"):
                yield Button("Ir a Configuracion", variant="primary", id="ir")
                yield Button("Ahora no", id="luego")

    def _resumen(self) -> str:
        """Lista los problemas con su arreglo, los que bloquean primero."""
        lineas = [
            "Scrappy puede explorar y calibrar sin esto, pero no publicara hasta que se resuelva:\n"
        ]

        for check in self._diagnosis.blocking:
            lineas.append(f"[b]{check.name}[/b]: {check.detail}")
            if check.fix:
                lineas.append(f"  → {check.fix}\n")

        avisos = self._diagnosis.warnings
        if avisos:
            lineas.append("\n[b]Ademas, conviene revisar:[/b]\n")
            for check in avisos:
                lineas.append(f"[b]{check.name}[/b]: {check.detail}")
                if check.fix:
                    lineas.append(f"  → {check.fix}\n")

        lineas.append(
            "\nTambien puedes hacerlo desde la terminal con [b]scrappy init[/b], "
            "que valida cada valor contra Telegram mientras lo escribes, o "
            "comprobarlo despues con [b]scrappy doctor[/b]."
        )
        return "\n".join(lineas)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("settings" if event.button.id == "ir" else None)

    def action_luego(self) -> None:
        self.dismiss(None)


def hace_falta_asistente(diagnosis: Diagnosis) -> bool:
    """True si hay algo que impide publicar.

    Los avisos por si solos no lo justifican: interrumpir con un asistente a
    quien solo tiene el User-Agent de Reddit sin personalizar seria molesto.
    """
    return any(check.status is CheckStatus.ERROR for check in diagnosis.checks)
