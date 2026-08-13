"""Ayuda: todas las teclas en un sitio.

Textual pinta los atajos de la pantalla actual en el pie, pero eso no incluye
los globales ni explica que hace cada uno. Esta pantalla si.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_AYUDA = """\
[b]Navegacion[/b]
  d          Panel: estado del sistema, fuentes y scheduler
  c          Candidatos: ver que publicaria y por que
  s          Configuracion: editar .env y sources.yaml
  ?          Esta ayuda
  r          Refrescar la pantalla actual
  R          Recargar la configuracion sin reiniciar (mayuscula)
  q          Salir

[b]En Candidatos[/b]
  e          Explorar (dry-run). No descarga ni publica nada
  p          Publicar, con confirmacion previa
  v          Vista previa del caption del item seleccionado
  flechas    Moverse por la tabla; el desglose se actualiza solo

[b]En Configuracion[/b]
  Ctrl+S     Guardar
  Ctrl+R     Descartar los cambios y releer los ficheros
  Tab        Moverse entre pestanas y campos

[b]Cuando corre Scrappy[/b]
  Solo mientras haya un proceso suyo vivo. Con esta ventana abierta y el
  scheduler arrancado, publica; al cerrarla, deja de publicar.
  Para que arranque solo al iniciar sesion, el Panel tiene un interruptor
  de arranque automatico.

[b]Cosas que conviene saber[/b]
  · El contenido descargado nunca se queda en la maquina: se publica en
    Telegram y se borra en el mismo ciclo.
  · Los comentarios de .env y sources.yaml se conservan al guardar: son la
    documentacion de cada valor.
  · Los secretos salen enmascarados. El interruptor «Mostrar secretos» los
    revela cuando hace falta comprobarlos.
  · Una clave que no este en el .env no queda desactivada: usa su valor por
    defecto, y la Configuracion lo muestra tal cual es.
  · Las horas se muestran en tu zona horaria. Lo que se guarda va en UTC.
  · Si algo no arranca, el detalle esta en data/scrappy-tui.log, y
    `scrappy doctor` explica que arreglar.

[dim]Pulsa Escape o ? para cerrar.[/dim]
"""


class HelpScreen(ModalScreen[None]):
    """Chuleta de teclas y recordatorios."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "cerrar", "Cerrar"),
        ("question_mark", "cerrar", "Cerrar"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ayuda-caja"):
            yield Static(_AYUDA, id="ayuda-texto")

    def action_cerrar(self) -> None:
        self.dismiss(None)
