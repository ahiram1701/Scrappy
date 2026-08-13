"""Arrancar Scrappy solo, al iniciar sesion.

Scrappy publica mientras haya un proceso suyo vivo, y no un minuto mas. Con la
forma habitual de usarlo -doble clic en `Scrappy.bat`- eso significa que
cerrar la ventana es dejar de publicar. Este modulo registra una tarea del
sistema para que no haga falta acordarse.

Tres decisiones sobre la tarea de Windows, todas con motivo:

- **Lanza `scrappy run`, no la TUI.** Abrir una ventana de interfaz al iniciar
  sesion seria molesto; lo que hace falta corriendo de fondo es el bot y el
  scheduler.
- **Sin ventana**, con `pythonw.exe`, que es el interprete sin consola. La
  alternativa habitual -un `.vbs` intermedio- anade un fichero mas que
  mantener para conseguir lo mismo.
- **Sin privilegios de administrador.** Una tarea de usuario al iniciar sesion
  no los necesita. Si el sistema los pidiera igualmente, se explica en vez de
  fallar en silencio.

Se registra por XML y no con los modificadores sueltos de `schtasks` porque el
directorio de trabajo importa: `.env` y `config/sources.yaml` se leen relativos
a el, y `/TR` no permite fijarlo.

En Linux y macOS esto no aplica: alli lo correcto es la unidad de systemd que
documenta `docs/DEPLOYMENT.md`, que ademas reinicia el proceso si se cae.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scrappy.observability.logging import get_logger

log = get_logger(__name__)

#: Nombre de la tarea en el Programador de tareas de Windows. Fijo a proposito:
#: es lo que hay que escribir para revisarla o borrarla a mano.
NOMBRE_TAREA = "Scrappy"

#: Un ejecutor recibe los argumentos y devuelve (codigo de salida, salida).
#: Se inyecta para poder probar sin tocar las tareas reales de nadie.
Ejecutor = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class EstadoAutoarranque:
    """Que se puede hacer y que esta hecho."""

    #: False en Linux y macOS: alli se usa systemd, no esto.
    disponible: bool
    #: La tarea existe y arrancara en el proximo inicio de sesion.
    activo: bool
    #: Frase para ensenar tal cual. Si algo no se puede, dice por que.
    detalle: str


def _ejecutar(args: list[str]) -> tuple[int, str]:
    """Lanza un comando y devuelve su codigo y su salida junta.

    `stderr` se une a `stdout` porque `schtasks` reparte sus mensajes entre los
    dos sin criterio claro, y lo que se quiere es el texto completo para
    poder ensenarlo.
    """
    # Los argumentos se construyen aqui, nunca vienen de fuera, y no hay shell
    # de por medio.
    proceso = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proceso.returncode, (proceso.stdout + proceso.stderr).strip()


class Autoarranque:
    """Gestiona la tarea de inicio de sesion del sistema."""

    def __init__(
        self,
        *,
        nombre: str = NOMBRE_TAREA,
        ejecutor: Ejecutor = _ejecutar,
        plataforma: str = sys.platform,
        directorio: Path | None = None,
        interprete: Path | None = None,
    ) -> None:
        self._nombre = nombre
        self._ejecutar = ejecutor
        self._plataforma = plataforma
        #: Directorio de trabajo de la tarea. Es donde estan `.env` y
        #: `config/`, asi que por defecto es desde donde se abrio la TUI.
        self._directorio = directorio or Path.cwd()
        self._interprete = interprete or _interprete_sin_consola()

    # ------------------------------------------------------------------
    @property
    def soportado(self) -> bool:
        return self._plataforma == "win32"

    def status(self) -> EstadoAutoarranque:
        """Si la tarea existe ahora mismo."""
        if not self.soportado:
            return EstadoAutoarranque(
                disponible=False,
                activo=False,
                detalle=(
                    "El autoarranque desde aqui es solo para Windows. En Linux y macOS "
                    "usa la unidad de systemd de docs/DEPLOYMENT.md, que ademas "
                    "reinicia el proceso si se cae."
                ),
            )

        codigo, salida = self._ejecutar(["schtasks", "/Query", "/TN", self._nombre])
        if codigo == 0:
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                detalle="Activo. Scrappy arranca solo al iniciar sesion, sin ventana.",
            )

        # `schtasks` devuelve un codigo distinto de cero tanto si la tarea no
        # existe como si algo fue mal. Distinguirlo importa: lo primero es lo
        # normal y lo segundo hay que contarlo.
        if _tarea_no_existe(salida):
            return EstadoAutoarranque(
                disponible=True,
                activo=False,
                detalle="Inactivo. Scrappy solo publica mientras lo tengas abierto.",
            )

        log.warning("autoarranque_consulta_fallida", codigo=codigo, salida=salida)
        return EstadoAutoarranque(
            disponible=True,
            activo=False,
            detalle=f"No se pudo consultar el Programador de tareas: {salida}",
        )

    def enable(self) -> EstadoAutoarranque:
        """Registra la tarea, sustituyendo la que hubiera."""
        if not self.soportado:
            return self.status()

        xml = _tarea_xml(self._interprete, self._directorio)

        # El Programador de tareas exige UTF-16 con BOM en el XML; con UTF-8 lo
        # rechaza sin explicar por que.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", encoding="utf-16", delete=False
        ) as fichero:
            fichero.write(xml)
            ruta_xml = Path(fichero.name)

        try:
            codigo, salida = self._ejecutar(
                ["schtasks", "/Create", "/TN", self._nombre, "/XML", str(ruta_xml), "/F"]
            )
        finally:
            ruta_xml.unlink(missing_ok=True)

        if codigo != 0:
            log.warning("autoarranque_alta_fallida", codigo=codigo, salida=salida)
            return EstadoAutoarranque(
                disponible=True,
                activo=False,
                detalle=_explicar_fallo(salida),
            )

        log.info("autoarranque_activado", directorio=str(self._directorio))
        return self.status()

    def disable(self) -> EstadoAutoarranque:
        """Borra la tarea. No detiene un Scrappy que ya este corriendo."""
        if not self.soportado:
            return self.status()

        codigo, salida = self._ejecutar(["schtasks", "/Delete", "/TN", self._nombre, "/F"])
        if codigo != 0 and not _tarea_no_existe(salida):
            log.warning("autoarranque_baja_fallida", codigo=codigo, salida=salida)
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                detalle=f"No se pudo quitar la tarea: {salida}",
            )

        log.info("autoarranque_desactivado")
        return EstadoAutoarranque(
            disponible=True,
            activo=False,
            detalle=(
                "Desactivado. Si Scrappy esta corriendo de fondo ahora mismo, "
                "seguira hasta que cierres la sesion."
            ),
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _interprete_sin_consola() -> Path:
    """`pythonw.exe` del entorno actual, o `python.exe` si no lo hay.

    `pythonw` es el mismo interprete sin ventana de consola. Sin el, iniciar
    sesion abriria una ventana negra que no se puede cerrar sin matar el bot.
    """
    actual = Path(sys.executable)
    silencioso = actual.with_name(actual.name.replace("python", "pythonw"))
    return silencioso if silencioso.exists() else actual


#: Como dice `schtasks` que la tarea no esta. Hay que mirar el texto porque
#: devuelve el mismo codigo de salida para «no existe» que para «algo fallo»,
#: y responde en el idioma del sistema. La lista sale de mensajes reales:
#: en un Windows en espanol la frase no es una traduccion literal de la
#: inglesa, asi que no vale con traducir a ojo.
_SIN_TAREA = (
    "cannot find the file specified",
    "no puede encontrar el archivo especificado",
    "does not exist",
    "no existe",
)


def _tarea_no_existe(salida: str) -> bool:
    """Si el mensaje de `schtasks` significa «no hay tal tarea»."""
    texto = salida.lower()
    return any(frase in texto for frase in _SIN_TAREA)


def _explicar_fallo(salida: str) -> str:
    """Convierte el error de `schtasks` en algo accionable."""
    texto = salida.lower()
    if "access is denied" in texto or "acceso denegado" in texto:
        return (
            "Windows denego el permiso para crear la tarea. Una tarea de usuario al "
            "iniciar sesion no deberia necesitar administrador; si tu equipo esta "
            "gestionado por una organizacion, puede haber una politica que lo impida. "
            f"Detalle: {salida}"
        )
    return f"No se pudo crear la tarea: {salida}"


def _tarea_xml(interprete: Path, directorio: Path) -> str:
    """XML de la tarea, con las cuatro cosas que hay que fijar.

    `RunLevel=LeastPrivilege` es deliberado: el bot no necesita permisos de
    administrador y pedirlos por si acaso es como no pedir ninguno.
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Scrappy: publica videos y memes en Telegram.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <!-- Un minuto de margen: al iniciar sesion la red aun no siempre esta. -->
      <Delay>PT1M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <!-- Sin limite de duracion: es un proceso de fondo, no una tarea que acaba. -->
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <!-- Que no lo pare al desenchufar el portatil ni al entrar en ahorro. -->
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{interprete}</Command>
      <Arguments>-m scrappy.cli run</Arguments>
      <WorkingDirectory>{directorio}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


__all__ = ["NOMBRE_TAREA", "Autoarranque", "EstadoAutoarranque"]
