"""Interfaz de terminal de Scrappy.

Es una interfaz mas, al mismo nivel que `cli.py` y `bot/`: no duplica logica,
consume `ScrappyApp`, que ya es el composition root del proyecto.

Se abre con `scrappy tui` o con doble clic en `Scrappy.bat`.
"""

from scrappy.tui.main import ScrappyTUI, run_tui

__all__ = ["ScrappyTUI", "run_tui"]
