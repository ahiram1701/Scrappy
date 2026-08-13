"""Pruebas del autoarranque.

Ninguna toca el Programador de tareas de verdad: `schtasks` se sustituye por
un doble que apunta lo que se le pide y devuelve lo que se le diga. Registrar
tareas reales en la maquina de quien ejecuta los tests seria inaceptable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrappy.autostart import NOMBRE_TAREA, Autoarranque
from tests.unit.test_tui import _texto

#: Lo que responde `schtasks /Query` cuando la tarea no esta. El mensaje viene
#: en el idioma del sistema, y el espanol NO es una traduccion literal del
#: ingles: los dos textos de abajo estan copiados de ejecuciones reales, y el
#: segundo destapo que la comprobacion original no lo reconocia.
NO_EXISTE_EN = "ERROR: The system cannot find the file specified."
NO_EXISTE_ES = "ERROR: El sistema no puede encontrar el archivo especificado."
#: Otra forma que usa `schtasks` segun la version.
NO_EXISTE_ALT = "ERROR: The specified task name does not exist in the system."


class SchtasksFalso:
    """Doble de `schtasks` que recuerda lo que se le pidio."""

    def __init__(self, *, existe: bool = False, fallo: tuple[int, str] | None = None) -> None:
        self.existe = existe
        self.llamadas: list[list[str]] = []
        self._fallo = fallo
        self._xml: str | None = None

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.llamadas.append(args)

        if self._fallo is not None and args[1] != "/Query":
            return self._fallo

        match args[1]:
            case "/Query":
                return (0, "Scrappy") if self.existe else (1, NO_EXISTE_EN)
            case "/Create":
                # Se lee aqui y no despues: `enable()` borra el temporal en su
                # `finally`, que es justo lo que debe hacer.
                ruta = Path(args[args.index("/XML") + 1])
                self._xml = ruta.read_text(encoding="utf-16")
                self.existe = True
                return 0, "SUCCESS"
            case "/Delete":
                if not self.existe:
                    return 1, NO_EXISTE_EN
                self.existe = False
                return 0, "SUCCESS"
            case _:  # pragma: no cover
                raise AssertionError(f"llamada inesperada: {args}")

    @property
    def xml_registrado(self) -> str:
        assert self._xml is not None, "no se registro ninguna tarea"
        return self._xml


#: Se comparan tal cual, sin normalizar separadores: cambiar las barras del XML
#: entero para compararlo tambien destroza las etiquetas de cierre.
DIRECTORIO = Path("C:/DEV/Scrappy")
INTERPRETE = DIRECTORIO / ".venv/Scripts/pythonw.exe"


def _crear(schtasks: SchtasksFalso, **kwargs: object) -> Autoarranque:
    return Autoarranque(
        ejecutor=schtasks,
        plataforma="win32",
        directorio=DIRECTORIO,
        interprete=INTERPRETE,
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def test_sin_tarea_registrada_dice_que_solo_publica_abierto() -> None:
    estado = _crear(SchtasksFalso(existe=False)).status()
    assert estado.disponible
    assert not estado.activo
    assert "mientras lo tengas abierto" in estado.detalle


def test_con_tarea_registrada_dice_que_esta_activo() -> None:
    estado = _crear(SchtasksFalso(existe=True)).status()
    assert estado.activo


def test_un_error_de_verdad_no_se_confunde_con_no_existe() -> None:
    """`schtasks` devuelve codigo distinto de cero en ambos casos.

    Tratar un fallo real como «no hay tarea» seria mentir: diria «inactivo»
    cuando en realidad no se ha podido saber.
    """
    estado = Autoarranque(
        ejecutor=lambda _args: (1, "ERROR: RPC server unavailable"),
        plataforma="win32",
    ).status()

    assert not estado.activo
    assert "No se pudo consultar" in estado.detalle


@pytest.mark.parametrize("mensaje", [NO_EXISTE_EN, NO_EXISTE_ES, NO_EXISTE_ALT])
def test_el_idioma_del_sistema_no_cambia_el_diagnostico(mensaje: str) -> None:
    """`schtasks` responde en el idioma del sistema, no en ingles.

    Sin esto, en un Windows en espanol la TUI decia «no se pudo consultar el
    Programador de tareas» donde solo pasaba que no habia ninguna tarea.
    """
    estado = Autoarranque(ejecutor=lambda _args: (1, mensaje), plataforma="win32").status()
    assert estado.disponible
    assert not estado.activo
    assert "mientras lo tengas abierto" in estado.detalle


# ---------------------------------------------------------------------------
# Alta y baja
# ---------------------------------------------------------------------------
def test_activar_registra_la_tarea() -> None:
    schtasks = SchtasksFalso()
    estado = _crear(schtasks).enable()

    assert estado.activo
    creacion = next(a for a in schtasks.llamadas if a[1] == "/Create")
    assert NOMBRE_TAREA in creacion
    # /F para sustituir una anterior en vez de fallar por duplicado.
    assert "/F" in creacion


def test_la_tarea_lanza_el_bot_y_no_la_interfaz() -> None:
    """Abrir una TUI al iniciar sesion seria una ventana molesta cada dia."""
    schtasks = SchtasksFalso()
    _crear(schtasks).enable()
    xml = schtasks.xml_registrado

    assert "<Arguments>-m scrappy.cli run</Arguments>" in xml
    assert "tui" not in xml


def test_la_tarea_usa_el_interprete_sin_consola() -> None:
    """Con `python.exe` quedaria una ventana negra que no se puede cerrar."""
    schtasks = SchtasksFalso()
    _crear(schtasks).enable()
    assert "pythonw.exe" in schtasks.xml_registrado


def test_la_tarea_fija_el_directorio_de_trabajo() -> None:
    """`.env` y `config/` se leen relativos a el; sin fijarlo no arranca."""
    schtasks = SchtasksFalso()
    _crear(schtasks).enable()
    assert f"<WorkingDirectory>{DIRECTORIO}</WorkingDirectory>" in schtasks.xml_registrado


def test_la_tarea_no_pide_privilegios_de_administrador() -> None:
    """Pedir permisos que no se necesitan es como no pedir ninguno."""
    schtasks = SchtasksFalso()
    _crear(schtasks).enable()
    assert "<RunLevel>LeastPrivilege</RunLevel>" in schtasks.xml_registrado


def test_desactivar_borra_la_tarea() -> None:
    schtasks = SchtasksFalso(existe=True)
    estado = _crear(schtasks).disable()

    assert not estado.activo
    assert any(a[1] == "/Delete" for a in schtasks.llamadas)


def test_desactivar_algo_que_no_estaba_no_es_un_error() -> None:
    """Pulsar «desactivar» dos veces no deberia dar un fallo."""
    estado = _crear(SchtasksFalso(existe=False)).disable()
    assert not estado.activo
    assert "Desactivado" in estado.detalle


def test_desactivar_avisa_de_que_no_mata_el_proceso_en_curso() -> None:
    estado = _crear(SchtasksFalso(existe=True)).disable()
    assert "seguira" in estado.detalle


def test_un_acceso_denegado_se_explica() -> None:
    """Un mensaje crudo de `schtasks` no le dice nada a nadie."""
    schtasks = SchtasksFalso(fallo=(1, "ERROR: Access is denied."))
    estado = _crear(schtasks).enable()

    assert not estado.activo
    assert "administrador" in estado.detalle
    assert "politica" in estado.detalle


# ---------------------------------------------------------------------------
# Fuera de Windows
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("plataforma", ["linux", "darwin"])
def test_fuera_de_windows_se_remite_a_systemd(plataforma: str) -> None:
    schtasks = SchtasksFalso()
    autoarranque = Autoarranque(ejecutor=schtasks, plataforma=plataforma)

    estado = autoarranque.status()
    assert not estado.disponible
    assert "systemd" in estado.detalle
    # Y sobre todo: no se ha llamado a nada.
    assert schtasks.llamadas == []


def test_fuera_de_windows_activar_no_hace_nada() -> None:
    schtasks = SchtasksFalso()
    estado = Autoarranque(ejecutor=schtasks, plataforma="linux").enable()

    assert not estado.disponible
    assert schtasks.llamadas == []


# ---------------------------------------------------------------------------
# El XML tiene que ser XML
# ---------------------------------------------------------------------------
def test_el_xml_generado_es_valido() -> None:
    """Un XML mal formado lo rechaza el Programador sin decir donde."""
    from xml.etree import ElementTree

    schtasks = SchtasksFalso()
    _crear(schtasks).enable()

    arbol = ElementTree.fromstring(schtasks.xml_registrado)
    assert arbol.tag.endswith("Task")


def test_el_xml_declara_utf16_como_exige_el_programador() -> None:
    schtasks = SchtasksFalso()
    _crear(schtasks).enable()
    assert re.match(r'<\?xml version="1.0" encoding="UTF-16"\?>', schtasks.xml_registrado)


# ---------------------------------------------------------------------------
# Como se ve en el panel
# ---------------------------------------------------------------------------
async def test_el_panel_ofrece_activar_solo_si_se_puede(tmp_path: Path) -> None:
    """Un boton que no puede funcionar tiene que estar deshabilitado.

    Con la tarea ya registrada, «Activar» no tiene nada que hacer; y fuera de
    Windows, ninguno de los dos.
    """
    from textual.widgets import Button

    from scrappy.tui.main import ScrappyTUI

    env_path = tmp_path / ".env"
    env_path.write_text("SCRAPPY_STATE_BACKEND=memory\n", encoding="utf-8")

    tui = ScrappyTUI(
        env_path=env_path,
        show_wizard=False,
        autoarranque=_crear(SchtasksFalso(existe=True)),
    )

    async with tui.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#autoarranque-on", Button).disabled
        assert not pilot.app.screen.query_one("#autoarranque-off", Button).disabled


async def test_fuera_de_windows_el_panel_no_ofrece_nada(tmp_path: Path) -> None:
    from textual.widgets import Button, Static

    from scrappy.tui.main import ScrappyTUI

    env_path = tmp_path / ".env"
    env_path.write_text("SCRAPPY_STATE_BACKEND=memory\n", encoding="utf-8")

    tui = ScrappyTUI(
        env_path=env_path,
        show_wizard=False,
        autoarranque=Autoarranque(ejecutor=SchtasksFalso(), plataforma="linux"),
    )

    async with tui.run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#autoarranque-on", Button).disabled
        assert pilot.app.screen.query_one("#autoarranque-off", Button).disabled
        # Y se dice que hacer en su lugar, en vez de dejar dos botones muertos.
        texto = _texto(pilot.app.screen.query_one("#estado-autoarranque", Static))
        assert "systemd" in texto
