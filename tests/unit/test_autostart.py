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

#: Lo que devuelve `schtasks /Query /XML` de una tarea de inicio de sesion. Se
#: mira el disparador y no el nombre, que es igual en los dos modos.
_XML_DE_SESION = "<Task><Triggers><LogonTrigger /></Triggers></Task>"


def _entrecomillado(texto: str, patron: str) -> str:
    """La primera ruta entre comillas simples que encaje con `patron`."""
    encontrado = re.search(rf"'({patron})'", texto)
    assert encontrado is not None, f"no hay ninguna ruta {patron} en: {texto}"
    return encontrado.group(1)


#: Lo que responde Windows cuando una cuenta normal intenta crear una tarea en
#: la carpeta raiz del Programador. No es un caso raro: es el caso corriente,
#: porque escribir ahi solo se le permite a un proceso elevado.
DENEGADO = "Error: Acceso denegado."


class SistemaFalso:
    """Doble de `schtasks` y de `powershell` que recuerda lo que se le pidio.

    Los dos en el mismo doble porque `enable()` los usa como una sola decision:
    si la tarea no se puede, va el acceso directo.
    """

    def __init__(
        self,
        *,
        existe: bool = False,
        fallo: tuple[int, str] | None = None,
        tarea_denegada: bool = False,
        borrado_denegado: bool = False,
        uac_rechazado: bool = False,
        acceso: Path | None = None,
    ) -> None:
        self.existe = existe
        self.llamadas: list[list[str]] = []
        self.guiones_elevados: list[str] = []
        self.xmls_elevados: list[str] = []
        self._fallo = fallo
        self._tarea_denegada = tarea_denegada
        self._borrado_denegado = borrado_denegado
        self._uac_rechazado = uac_rechazado
        self._acceso = acceso
        self._xml: str | None = None
        self._guion: str | None = None

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.llamadas.append(args)

        if args[0] == "powershell":
            if "Start-Process" in args[-1]:
                return self._elevado(args[-1])
            return self._acceso_directo(args[-1])

        if self._fallo is not None and args[1] != "/Query":
            return self._fallo

        match args[1]:
            case "/Query":
                # Se devuelve el XML porque de ahi sale si la tarea arranca con
                # el equipo o con la sesion, que es lo que mira `status()`.
                return (0, self._xml or _XML_DE_SESION) if self.existe else (1, NO_EXISTE_EN)
            case "/Create":
                if self._tarea_denegada:
                    return 1, DENEGADO
                # Se lee aqui y no despues: `enable()` borra el temporal en su
                # `finally`, que es justo lo que debe hacer.
                ruta = Path(args[args.index("/XML") + 1])
                self._xml = ruta.read_text(encoding="utf-16")
                self.existe = True
                return 0, "SUCCESS"
            case "/Delete":
                if self._borrado_denegado:
                    return 1, DENEGADO
                if not self.existe:
                    return 1, NO_EXISTE_EN
                self.existe = False
                self._xml = None
                return 0, "SUCCESS"
            case _:  # pragma: no cover
                raise AssertionError(f"llamada inesperada: {args}")

    def _elevado(self, comando: str) -> tuple[int, str]:
        """Hace de UAC: lee el guion que se iba a correr elevado y lo obedece.

        Se lee de verdad en vez de darlo por bueno porque lo que importa es
        **que** se registra: una tarea de arranque del equipo y no otra cosa.
        """
        guion = Path(_entrecomillado(comando, r"[^']+\.ps1")).read_text(encoding="utf-8")
        self.guiones_elevados.append(guion)

        if self._uac_rechazado:
            return 1, ""

        if "/Delete" in guion:
            self.existe = False
            self._xml = None
            return 0, "SUCCESS"

        # Se guardan todos los XML del guion, no solo el que se registra: el
        # segundo es el respaldo, y que exista es parte de lo que se prueba.
        self.xmls_elevados = [
            Path(ruta).read_text(encoding="utf-16")
            for ruta in re.findall(r"/XML '([^']+\.xml)'", guion)
        ]
        self._xml = self.xmls_elevados[0]
        self.existe = True
        return 0, "SUCCESS"

    def _acceso_directo(self, guion: str) -> tuple[int, str]:
        """Hace lo que haria `WScript.Shell`: dejar el fichero puesto."""
        if self._fallo is not None:
            return self._fallo
        self._guion = guion
        if self._acceso is not None:
            self._acceso.parent.mkdir(parents=True, exist_ok=True)
            self._acceso.write_bytes(b"lnk de mentira")
        return 0, ""

    @property
    def xml_registrado(self) -> str:
        assert self._xml is not None, "no se registro ninguna tarea"
        return self._xml

    @property
    def guion_acceso(self) -> str:
        assert self._guion is not None, "no se creo ningun acceso directo"
        return self._guion


#: El nombre de antes, que es como lo llaman las pruebas de la tarea.
SchtasksFalso = SistemaFalso


#: Se comparan tal cual, sin normalizar separadores: cambiar las barras del XML
#: entero para compararlo tambien destroza las etiquetas de cierre.
DIRECTORIO = Path("C:/DEV/Scrappy")
INTERPRETE = DIRECTORIO / ".venv/Scripts/pythonw.exe"


@pytest.fixture(autouse=True)
def _inicio_de_mentira(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ninguna prueba toca la carpeta de Inicio de verdad de nadie.

    Es autouse porque el respaldo entra solo en cuanto la tarea falla, y un
    olvido dejaria a Scrappy arrancando en la maquina de quien pase los tests.
    """
    carpeta = tmp_path / "Inicio"
    monkeypatch.setattr("scrappy.autostart._carpeta_inicio", lambda: carpeta)
    return carpeta


def _crear(schtasks: SistemaFalso, **kwargs: object) -> Autoarranque:
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
    """Correr elevado por si acaso es como no elegir ningun nivel.

    Otra cosa es *crearla*, que si los pide: de ahi el respaldo de mas abajo.
    """
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


def test_si_fallan_los_dos_se_explican_los_dos() -> None:
    """Un mensaje crudo de `schtasks` no le dice nada a nadie.

    Y contar solo el de la tarea seria peor todavia: callaria que el respaldo
    tampoco salio, que es lo que explica por que no hay autoarranque.
    """
    schtasks = SchtasksFalso(fallo=(1, "ERROR: Access is denied."))
    estado = _crear(schtasks).enable()

    assert not estado.activo
    assert "administrador" in estado.detalle
    assert "acceso directo" in estado.detalle
    assert "ERROR: Access is denied." in estado.detalle


# ---------------------------------------------------------------------------
# El respaldo: la carpeta de Inicio
# ---------------------------------------------------------------------------
def test_si_windows_no_deja_crear_la_tarea_queda_el_acceso_directo(
    _inicio_de_mentira: Path,
) -> None:
    """Es el caso corriente, no el raro.

    Crear una tarea escribe en la carpeta raiz del Programador, y eso Windows
    solo se lo permite a un proceso elevado. La TUI no lo es, asi que sin este
    respaldo el boton «Activar» no funcionaria en una cuenta normal.
    """
    acceso = _inicio_de_mentira / "Scrappy.lnk"
    sistema = SistemaFalso(tarea_denegada=True, acceso=acceso)

    estado = _crear(sistema, carpeta_inicio=_inicio_de_mentira).enable()

    assert estado.activo
    assert acceso.exists()
    assert "carpeta de Inicio" in estado.detalle


def test_el_acceso_directo_arranca_el_bot_sin_ventana(_inicio_de_mentira: Path) -> None:
    """Lo mismo que la tarea: `pythonw`, `run` y el directorio del `.env`."""
    acceso = _inicio_de_mentira / "Scrappy.lnk"
    sistema = SistemaFalso(tarea_denegada=True, acceso=acceso)

    _crear(sistema, carpeta_inicio=_inicio_de_mentira).enable()
    guion = sistema.guion_acceso

    assert "pythonw.exe" in guion
    assert "-m scrappy.cli run" in guion
    assert str(DIRECTORIO) in guion
    assert "tui" not in guion


def test_la_tarea_tiene_preferencia_sobre_el_acceso_directo(
    _inicio_de_mentira: Path,
) -> None:
    """La tarea espera a que haya red y reintenta; el acceso directo no."""
    acceso = _inicio_de_mentira / "Scrappy.lnk"
    sistema = SistemaFalso(acceso=acceso)

    _crear(sistema, carpeta_inicio=_inicio_de_mentira).enable()

    assert not acceso.exists()
    assert not any(a[0] == "powershell" for a in sistema.llamadas)


def test_con_el_acceso_directo_puesto_el_estado_es_activo(
    _inicio_de_mentira: Path,
) -> None:
    """Sin esto el panel diria «inactivo» con Scrappy arrancando cada dia."""
    acceso = _inicio_de_mentira / "Scrappy.lnk"
    acceso.parent.mkdir(parents=True, exist_ok=True)
    acceso.write_bytes(b"lnk de mentira")

    estado = _crear(SistemaFalso(existe=False), carpeta_inicio=_inicio_de_mentira).status()

    assert estado.activo
    assert "carpeta de Inicio" in estado.detalle


def test_desactivar_quita_tambien_el_acceso_directo(_inicio_de_mentira: Path) -> None:
    """Cual quedo puesto depende de los permisos que hubiera al activarlo.

    Quitar solo la tarea dejaria a Scrappy arrancando solo despues de pulsar
    «Desactivar», que es exactamente lo contrario de lo que se pidio.
    """
    acceso = _inicio_de_mentira / "Scrappy.lnk"
    acceso.parent.mkdir(parents=True, exist_ok=True)
    acceso.write_bytes(b"lnk de mentira")

    estado = _crear(SistemaFalso(existe=True), carpeta_inicio=_inicio_de_mentira).disable()

    assert not estado.activo
    assert not acceso.exists()


def test_una_ruta_con_apostrofo_no_rompe_el_guion(_inicio_de_mentira: Path) -> None:
    """PowerShell escapa las comillas simples doblandolas, no con barra."""
    from scrappy.autostart import _guion_acceso

    guion = _guion_acceso(
        Path("C:/Users/O'Brien/Scrappy.lnk"),
        Path("C:/O'Brien/pythonw.exe"),
        Path("C:/O'Brien/Scrappy"),
    )

    assert "O''Brien" in guion
    assert "O'Brien'" not in guion


# ---------------------------------------------------------------------------
# Sin iniciar sesion
# ---------------------------------------------------------------------------
def test_sin_iniciar_sesion_arranca_con_el_equipo() -> None:
    """Es la diferencia entera entre los dos modos.

    Con `LogonTrigger`, un equipo encendido y la sesion cerrada no publica
    nada, que es justo lo que se queria evitar.
    """
    sistema = SistemaFalso()
    estado = _crear(sistema).enable("sistema")

    assert estado.activo
    assert estado.modo == "sistema"
    assert "<BootTrigger>" in sistema.xml_registrado
    assert "<LogonTrigger>" not in sistema.xml_registrado


def test_sin_iniciar_sesion_se_registra_elevado_y_de_una_vez() -> None:
    """Cada llamada elevada es un dialogo de UAC.

    Dos seguidos para una sola cosa se parecen demasiado a algo que no
    deberias aceptar, asi que las dos identidades van en el mismo guion.
    """
    sistema = SistemaFalso()
    _crear(sistema).enable("sistema")

    assert len(sistema.guiones_elevados) == 1
    guion = sistema.guiones_elevados[0]
    assert guion.count("/Create") == 2
    assert "$LASTEXITCODE" in guion


def test_sin_iniciar_sesion_corre_como_tu_cuenta_sin_guardar_la_contrasena() -> None:
    """`S4U` es «esta cuenta, sin sesion y sin contrasena guardada».

    Correr como SYSTEM funciona igual y da mas privilegios de los que hacen
    falta, asi que es el segundo intento, no el primero.
    """
    sistema = SistemaFalso()
    _crear(sistema).enable("sistema")

    assert "<LogonType>S4U</LogonType>" in sistema.xml_registrado
    assert "<RunLevel>LeastPrivilege</RunLevel>" in sistema.xml_registrado


def test_si_tu_cuenta_no_admite_s4u_queda_system_como_respaldo() -> None:
    """Las cuentas de Microsoft y algunas politicas rechazan S4U.

    Sin respaldo, «sin iniciar sesion» no funcionaria en esas maquinas y el
    fallo aparecerian meses despues, la primera vez que alguien reiniciara.
    """
    sistema = SistemaFalso()
    _crear(sistema).enable("sistema")

    principal, respaldo = sistema.xmls_elevados
    assert "<LogonType>S4U</LogonType>" in principal
    # S-1-5-18 es SYSTEM por SID, que no depende del idioma del sistema.
    assert "S-1-5-18" in respaldo
    assert "<BootTrigger>" in respaldo


def test_si_se_rechaza_el_uac_se_dice_que_hacer() -> None:
    """No hay respaldo posible: sin elevacion no existe «sin iniciar sesion»."""
    sistema = SistemaFalso(uac_rechazado=True)
    estado = _crear(sistema).enable("sistema")

    assert not estado.activo
    assert "UAC" in estado.detalle
    # Y se ofrece lo que si se puede hacer sin permisos.
    assert "iniciar sesion" in estado.detalle


def test_una_tarea_de_arranque_se_reconoce_como_tal() -> None:
    """El panel tiene que poder decir cual de los dos modos esta puesto."""
    sistema = SistemaFalso()
    autoarranque = _crear(sistema)
    autoarranque.enable("sistema")

    estado = autoarranque.status()
    assert estado.modo == "sistema"
    assert "sin iniciar sesion" in estado.detalle.lower()


def test_quitar_una_tarea_elevada_vuelve_a_pedir_permiso() -> None:
    """Borrarla pide lo mismo que crearla, y es correcto que lo pida.

    Quitar el autoarranque del equipo entero no deberia poder hacerse a
    hurtadillas desde un proceso sin permisos.
    """
    sistema = SistemaFalso(existe=True, borrado_denegado=True)
    estado = _crear(sistema).disable()

    assert not estado.activo
    assert any("/Delete" in guion for guion in sistema.guiones_elevados)


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
        # Con el de sesion puesto, «sin iniciar sesion» sigue ofreciendose: es
        # un ascenso, no un duplicado.
        assert not pilot.app.screen.query_one("#autoarranque-sistema", Button).disabled


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
        assert pilot.app.screen.query_one("#autoarranque-sistema", Button).disabled
        # Y se dice que hacer en su lugar, en vez de dejar tres botones muertos.
        texto = _texto(pilot.app.screen.query_one("#estado-autoarranque", Static))
        assert "systemd" in texto
