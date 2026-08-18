"""Arrancar Scrappy solo, sin tener que acordarse.

Scrappy publica mientras haya un proceso suyo vivo, y no un minuto mas. Con la
forma habitual de usarlo -doble clic en `Scrappy.bat`- eso significa que
cerrar la ventana es dejar de publicar. Este modulo lo deja arrancando solo.

Dos decisiones sobre que se arranca, las dos con motivo:

- **Lanza `scrappy run`, no la TUI.** Abrir una ventana de interfaz al arrancar
  seria molesto; lo que hace falta corriendo de fondo es el bot y el scheduler.
- **Sin ventana**, con `pythonw.exe`, que es el interprete sin consola. Ojo:
  ahi `sys.stdout` es None, y por eso los logs se van a fichero solos; lo
  resuelve `configure_logging`, que sin esa red moria al arrancar.

## Dos modos, y por que hacen falta los dos

- **`sesion`** (el de por defecto): arranca **al iniciar sesion**. No pide
  permisos, pero mientras nadie entre al equipo Scrappy no publica.
- **`sistema`**: arranca **al encender el equipo**, aunque no entre nadie. Es
  lo que se quiere para dejarlo publicando de verdad, y **pide elevacion**: una
  tarea que corre sin sesion se registra con privilegios de administrador y no
  hay forma de rodearlo. Sale el dialogo de UAC, se acepta, y ya.

## Tres mecanismos, por orden de calidad

1. **Tarea de arranque del sistema** (`BootTrigger`). La unica que cumple «sin
   iniciar sesion». Corre como el propio usuario con `S4U`, que es «esta
   cuenta, sin sesion y sin guardar su contrasena»; si el sistema no admite
   S4U -pasa con cuentas de Microsoft- se cae a `SYSTEM`, que siempre puede.
2. **Tarea de inicio de sesion** (`LogonTrigger`). Retrasa el arranque un
   minuto -la red no siempre esta lista- y reintenta si el proceso muere.
3. **Acceso directo en la carpeta de Inicio**. El respaldo sin permisos: la
   carpeta es del usuario y no hay que pedirle nada a nadie. Lo que se pierde
   es el minuto de margen y el reintento, que se compensan en `scrappy run`
   insistiendo con Telegram.

Las tareas se registran por XML y no con los modificadores sueltos de
`schtasks` porque el directorio de trabajo importa: `.env` y
`config/sources.yaml` se leen relativos a el, y `/TR` no permite fijarlo.

## Sobre los permisos

Crear **cualquier** tarea escribe en la carpeta raiz del Programador, y eso
Windows solo se lo permite a un proceso elevado. Ser administrador no basta:
el token de una sesion normal lleva el grupo de administradores «solo para
denegar» hasta que algo pide elevacion. La TUI no la pide -no deberia- asi que
el modo `sesion` se queda en el acceso directo, y el modo `sistema`, que no
tiene respaldo posible, la pide por UAC.

En Linux y macOS esto no aplica: alli lo correcto es la unidad de systemd que
documenta `docs/DEPLOYMENT.md`, que ademas reinicia el proceso si se cae.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from scrappy.observability.logging import get_logger

log = get_logger(__name__)

#: Nombre de la tarea en el Programador de tareas de Windows. Fijo a proposito:
#: es lo que hay que escribir para revisarla o borrarla a mano.
NOMBRE_TAREA = "Scrappy"

#: Como se llama el acceso directo del respaldo. Lleva el mismo nombre que la
#: tarea para que quien mire cualquiera de los dos sitios reconozca que es.
NOMBRE_ACCESO = f"{NOMBRE_TAREA}.lnk"

#: Cuando arranca Scrappy. `sesion` al entrar al equipo; `sistema` al
#: encenderlo, entre alguien o no.
Modo = Literal["sesion", "sistema"]

#: Un ejecutor recibe los argumentos y devuelve (codigo de salida, salida).
#: Se inyecta para poder probar sin tocar las tareas reales de nadie.
Ejecutor = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class EstadoAutoarranque:
    """Que se puede hacer y que esta hecho."""

    #: False en Linux y macOS: alli se usa systemd, no esto.
    disponible: bool
    #: Algo arrancara Scrappy solo: una tarea o el acceso directo.
    activo: bool
    #: Frase para ensenar tal cual. Si algo no se puede, dice por que.
    detalle: str
    #: Con que se arranca, si se arranca. Lo mira el panel para saber si
    #: ofrecer «sin iniciar sesion» o si eso ya esta puesto.
    modo: Modo | None = None


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
        carpeta_inicio: Path | None = None,
    ) -> None:
        self._nombre = nombre
        self._ejecutar = ejecutor
        self._plataforma = plataforma
        #: Directorio de trabajo de la tarea. Es donde estan `.env` y
        #: `config/`, asi que por defecto es desde donde se abrio la TUI.
        self._directorio = directorio or Path.cwd()
        self._interprete = interprete or _interprete_sin_consola()
        #: El acceso directo del respaldo, en la carpeta de Inicio del usuario.
        self._acceso = (carpeta_inicio or _carpeta_inicio()) / NOMBRE_ACCESO

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

        # Se pide el XML y no solo el nombre porque de la tarea hay que saber
        # dos cosas: si esta, y si arranca con el equipo o con la sesion.
        codigo, salida = self._ejecutar(["schtasks", "/Query", "/TN", self._nombre, "/XML", "ONE"])
        if codigo == 0:
            if _arranca_con_el_equipo(salida):
                return EstadoAutoarranque(
                    disponible=True,
                    activo=True,
                    modo="sistema",
                    detalle=(
                        "Activo sin iniciar sesion. Scrappy arranca al encender el "
                        "equipo, entre alguien o no, y sin ventana."
                    ),
                )
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                modo="sesion",
                detalle=(
                    "Activo (tarea programada). Scrappy arranca al iniciar sesion, "
                    "sin ventana. Mientras nadie entre al equipo, no publica."
                ),
            )

        # `schtasks` devuelve un codigo distinto de cero tanto si la tarea no
        # existe como si algo fue mal. Distinguirlo importa: lo primero es lo
        # normal y lo segundo hay que contarlo.
        if not _tarea_no_existe(salida):
            log.warning("autoarranque_consulta_fallida", codigo=codigo, salida=salida)
            return EstadoAutoarranque(
                disponible=True,
                activo=False,
                detalle=f"No se pudo consultar el Programador de tareas: {salida}",
            )

        # Sin tarea queda mirar el respaldo, que es lo que hay cuando se activo
        # desde la TUI: sin elevacion el Programador no deja crear nada.
        if self._acceso.exists():
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                modo="sesion",
                detalle=(
                    "Activo (carpeta de Inicio). Scrappy arranca al iniciar sesion, "
                    "sin ventana. Mientras nadie entre al equipo, no publica."
                ),
            )

        return EstadoAutoarranque(
            disponible=True,
            activo=False,
            detalle="Inactivo. Scrappy solo publica mientras lo tengas abierto.",
        )

    def enable(self, modo: Modo = "sesion") -> EstadoAutoarranque:
        """Deja Scrappy arrancando solo, por el mejor medio disponible.

        Con `modo="sistema"` arranca al encender el equipo aunque no entre
        nadie, que es lo unico que cumple «sin iniciar sesion». Eso **pide
        elevacion** y no tiene respaldo: si se rechaza el UAC, no queda nada
        que intentar y se dice tal cual.

        Con `modo="sesion"` se intenta primero la tarea programada y, si
        Windows no la deja crear -lo normal sin elevacion-, el acceso directo
        en la carpeta de Inicio. Ese orden y no otro: la tarea espera a que
        haya red y reintenta si el proceso muere, y el acceso directo no hace
        ninguna de las dos cosas.
        """
        if not self.soportado:
            return self.status()

        if modo == "sistema":
            return self._activar_sin_sesion()

        codigo, salida = self._alta_tarea()
        if codigo == 0:
            log.info(
                "autoarranque_activado",
                metodo="tarea_programada",
                directorio=str(self._directorio),
            )
            return self.status()

        # No es un fallo que haya que ensenar: en una cuenta normal es lo
        # esperado, y para eso esta el respaldo.
        log.info("autoarranque_tarea_no_disponible", codigo=codigo, salida=salida)

        codigo_acceso, salida_acceso = self._alta_acceso()
        if codigo_acceso == 0 and self._acceso.exists():
            log.info(
                "autoarranque_activado",
                metodo="carpeta_inicio",
                directorio=str(self._directorio),
            )
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                modo="sesion",
                detalle=(
                    "Activo (carpeta de Inicio). Scrappy arranca al iniciar sesion, "
                    "sin ventana. El Programador de tareas no deja crear la tarea sin "
                    "elevacion, asi que se uso tu carpeta de Inicio, que arranca lo "
                    "mismo. Mientras nadie entre al equipo, no publica."
                ),
            )

        log.warning(
            "autoarranque_alta_fallida",
            salida_tarea=salida,
            salida_acceso=salida_acceso,
        )
        return EstadoAutoarranque(
            disponible=True,
            activo=False,
            detalle=_explicar_fallo(salida, salida_acceso),
        )

    def disable(self) -> EstadoAutoarranque:
        """Quita los dos. No detiene un Scrappy que ya este corriendo.

        Los dos aunque solo hubiera uno: cual quedo puesto depende de con que
        permisos se activo, y dejarse el otro seria seguir arrancando solo
        despues de haber pulsado «Desactivar».
        """
        if not self.soportado:
            return self.status()

        problemas: list[str] = []

        codigo, salida = self._ejecutar(["schtasks", "/Delete", "/TN", self._nombre, "/F"])
        if codigo != 0 and _sin_permiso(salida):
            # La tarea de «sin iniciar sesion» se creo elevada, asi que borrarla
            # tambien lo pide. Sale otro UAC, y es correcto que salga: quitar el
            # autoarranque del equipo entero no deberia poder hacerse a hurtadillas.
            codigo, salida = self._elevado(
                f"schtasks /Delete /TN {_ps(self._nombre)} /F 2>&1 | Out-String"
            )
            if codigo != 0 and not _tarea_no_existe(salida):
                problemas.append(f"la tarea programada ({salida})")
        elif codigo != 0 and not _tarea_no_existe(salida):
            problemas.append(f"la tarea programada ({salida})")

        try:
            self._acceso.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - permisos raros en Inicio
            problemas.append(f"el acceso directo de Inicio ({exc})")

        if problemas:
            log.warning("autoarranque_baja_fallida", problemas=problemas)
            return EstadoAutoarranque(
                disponible=True,
                activo=True,
                detalle=f"No se pudo quitar {' ni '.join(problemas)}.",
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

    # ------------------------------------------------------------------
    def _activar_sin_sesion(self) -> EstadoAutoarranque:
        """Registra la tarea de arranque del equipo, con elevacion.

        Se intentan dos identidades en la misma llamada elevada -y no en dos-
        porque cada llamada elevada es un dialogo de UAC, y dos seguidos para
        una sola cosa se parecen mucho a algo que no deberias aceptar.
        """
        codigo, salida = self._alta_tarea_elevada()

        estado = self.status()
        if estado.activo and estado.modo == "sistema":
            log.info(
                "autoarranque_activado",
                metodo="tarea_sistema",
                directorio=str(self._directorio),
            )
            return estado

        log.warning("autoarranque_sin_sesion_fallido", codigo=codigo, salida=salida)
        return EstadoAutoarranque(
            disponible=True,
            activo=estado.activo,
            modo=estado.modo,
            detalle=_explicar_fallo_elevado(salida),
        )

    def _alta_tarea_elevada(self) -> tuple[int, str]:
        """La llamada elevada: primero como tu cuenta, y si no, como SYSTEM.

        `S4U` es «esta cuenta, sin sesion iniciada y sin guardar su
        contrasena». Es lo que hay que querer: mismos permisos que tu, mismos
        duenos en los ficheros que escriba. No siempre se puede -las cuentas de
        Microsoft y algunas politicas lo rechazan- y ahi entra `SYSTEM`, que
        arranca igual pero con mas privilegios de los necesarios.
        """
        xml_usuario = _tarea_xml(
            self._interprete, self._directorio, modo="sistema", usuario=_usuario_actual()
        )
        xml_sistema = _tarea_xml(self._interprete, self._directorio, modo="sistema", usuario=None)

        with _xml_temporal(xml_usuario) as ruta_usuario, _xml_temporal(xml_sistema) as ruta_sistema:
            return self._elevado(
                f"$salida = schtasks /Create /TN {_ps(self._nombre)} "
                f"/XML {_ps(ruta_usuario)} /F 2>&1 | Out-String; "
                "if ($LASTEXITCODE -ne 0) { "
                f"$salida += schtasks /Create /TN {_ps(self._nombre)} "
                f"/XML {_ps(ruta_sistema)} /F 2>&1 | Out-String }}; "
                "$salida"
            )

    def _elevado(self, guion: str) -> tuple[int, str]:
        """Corre un trozo de PowerShell como administrador, via UAC.

        La salida del proceso elevado no se puede leer por una tuberia -es otro
        proceso, lanzado por el sistema- asi que se le pide que la escriba en un
        fichero y se lee de ahi. Sin eso, un fallo dentro del UAC seria un
        codigo de salida a secas.
        """
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ps1", encoding="utf-8", delete=False
        ) as fichero:
            ruta_guion = Path(fichero.name)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fichero:
            ruta_salida = Path(fichero.name)

        ruta_guion.write_text(
            f"{guion} | Out-File -FilePath {_ps(ruta_salida)} -Encoding utf8\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )

        try:
            codigo, salida = self._ejecutar(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$p = Start-Process -FilePath 'powershell' -ArgumentList "
                    "'-NoProfile','-ExecutionPolicy','Bypass','-File',"
                    f"{_ps(ruta_guion)} -Verb RunAs -Wait -PassThru "
                    "-WindowStyle Hidden; exit $p.ExitCode",
                ]
            )
            detalle = ruta_salida.read_text(encoding="utf-8", errors="replace").strip()
            return codigo, (detalle or salida)
        finally:
            ruta_guion.unlink(missing_ok=True)
            ruta_salida.unlink(missing_ok=True)

    def _alta_tarea(self) -> tuple[int, str]:
        """Registra la tarea de inicio de sesion, sustituyendo la que hubiera."""
        with _xml_temporal(_tarea_xml(self._interprete, self._directorio)) as ruta_xml:
            return self._ejecutar(
                ["schtasks", "/Create", "/TN", self._nombre, "/XML", str(ruta_xml), "/F"]
            )

    def _alta_acceso(self) -> tuple[int, str]:
        """Crea el acceso directo en la carpeta de Inicio del usuario.

        Un `.lnk` es un formato binario que Python no sabe escribir, pero
        Windows si: `WScript.Shell` lleva ahi desde siempre y no hay que
        instalar nada. Un `.bat` en su lugar abriria una ventana de consola al
        iniciar sesion, que es justo lo que se quiere evitar.
        """
        self._acceso.parent.mkdir(parents=True, exist_ok=True)
        return self._ejecutar(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _guion_acceso(self._acceso, self._interprete, self._directorio),
            ]
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
    # Dos candidatos: el nombre de este interprete con la «w» -que cubre
    # `python.exe` y tambien `python3.13.exe`- y el `pythonw.exe` de al lado,
    # que es como se llama siempre en un venv.
    for candidato in (
        actual.with_name(actual.name.replace("python", "pythonw")),
        actual.with_name("pythonw.exe"),
    ):
        if candidato.exists():
            return candidato
    return actual


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


def _usuario_actual() -> str:
    """`DOMINIO\\usuario`, que es como el Programador identifica una cuenta."""
    dominio = os.environ.get("USERDOMAIN", "")
    usuario = os.environ.get("USERNAME", "")
    return f"{dominio}\\{usuario}" if dominio else usuario


def _ps(valor: object) -> str:
    """Cita un valor para PowerShell: comillas simples, las suyas dobladas.

    Ninguno viene de fuera, pero una carpeta con un apostrofo en el nombre es
    perfectamente legal y romperia el guion.
    """
    return "'" + str(valor).replace("'", "''") + "'"


@contextmanager
def _xml_temporal(xml: str) -> Iterator[Path]:
    """Deja el XML en un fichero mientras se usa, y lo borra despues.

    El Programador de tareas exige UTF-16 con BOM; con UTF-8 lo rechaza sin
    explicar por que.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as f:
        f.write(xml)
        ruta = Path(f.name)
    try:
        yield ruta
    finally:
        ruta.unlink(missing_ok=True)


def _arranca_con_el_equipo(xml: str) -> bool:
    """Si el XML de una tarea la dispara al encender, no al iniciar sesion.

    Los nulos se quitan porque `schtasks /XML` escribe UTF-16 y se lee como
    texto suelto: sin esto, «BootTrigger» llega con un nulo entre cada letra y
    no lo reconoceria nadie.
    """
    return "boottrigger" in xml.replace("\x00", "").lower()


def _carpeta_inicio() -> Path:
    """La carpeta de Inicio del usuario, la de toda la vida.

    Se compone a mano desde `APPDATA` en vez de preguntarle a la shell: es una
    ruta fija desde hace decadas y consultarla por COM seria un proceso mas
    para averiguar lo que ya se sabe.
    """
    return Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _guion_acceso(acceso: Path, interprete: Path, directorio: Path) -> str:
    """PowerShell que escribe el `.lnk`.

    Las rutas van entre comillas simples con las suyas dobladas, que es como se
    escapa en PowerShell. Ninguna viene de fuera, pero una carpeta con un
    apostrofo en el nombre es perfectamente legal y romperia el guion.
    """

    def cita(valor: object) -> str:
        return "'" + str(valor).replace("'", "''") + "'"

    return (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + cita(acceso) + "); "
        "$s.TargetPath = " + cita(interprete) + "; "
        "$s.Arguments = '-m scrappy.cli run'; "
        "$s.WorkingDirectory = " + cita(directorio) + "; "
        "$s.Description = 'Scrappy: publica videos y memes en Telegram.'; "
        "$s.Save()"
    )


def _sin_permiso(salida: str) -> bool:
    """Si Windows respondio «acceso denegado», en el idioma que sea."""
    texto = salida.lower()
    return "access is denied" in texto or "acceso denegado" in texto


def _explicar_fallo_elevado(salida: str) -> str:
    """El fallo del modo «sin iniciar sesion», que no tiene respaldo posible."""
    texto = salida.lower()
    if "canceled by the user" in texto or "cancelada por el usuario" in texto or not texto:
        return (
            "No se registro nada: hace falta aceptar el aviso de Windows (UAC) para "
            "crear una tarea que arranque sin iniciar sesion. Si lo rechazaste sin "
            "querer, vuelve a pulsarlo. Mientras tanto puedes dejarlo arrancando al "
            "iniciar sesion, que no pide permisos."
        )
    return (
        "No se pudo crear la tarea de arranque del equipo. Puedes dejarlo arrancando "
        f"al iniciar sesion, que no pide permisos. Detalle: {salida}"
    )


def _explicar_fallo(salida_tarea: str, salida_acceso: str) -> str:
    """Convierte los dos errores en una sola frase accionable.

    Se cuentan los dos porque fallaron los dos: quedarse con el primero diria
    «no se pudo crear la tarea» y callaria que el respaldo tampoco salio, que
    es la parte que explica por que no hay autoarranque.
    """
    if _sin_permiso(salida_tarea):
        motivo = (
            "Windows no deja crear la tarea programada sin permisos de administrador, "
            "y el acceso directo en tu carpeta de Inicio tampoco se pudo crear"
        )
    else:
        motivo = "No se pudo crear ni la tarea programada ni el acceso directo de Inicio"

    return f"{motivo}. Tarea: {salida_tarea}. Acceso directo: {salida_acceso}"


def _tarea_xml(
    interprete: Path,
    directorio: Path,
    *,
    modo: Modo = "sesion",
    usuario: str | None = None,
) -> str:
    """XML de la tarea, con las cuatro cosas que hay que fijar.

    `RunLevel=LeastPrivilege` es deliberado: el bot no necesita permisos de
    administrador y pedirlos por si acaso es como no pedir ninguno. Ojo con
    confundirlo con la elevacion que hace falta para **crear** la tarea, que es
    otra cosa y no se hereda.

    Args:
        modo: `sesion` dispara al iniciar sesion; `sistema`, al encender el
            equipo, que es lo que corre sin que entre nadie.
        usuario: solo en modo `sistema`. Con una cuenta se usa `S4U` -sin
            sesion y sin guardar contrasena-; sin ella, `SYSTEM`.

    Las rutas van escapadas -un `&` en el nombre de una carpeta es raro pero
    legal- y el orden de `<Settings>` es el del esquema de Windows, que no
    admite reordenarlo.
    """
    if modo == "sistema":
        disparador = """    <BootTrigger>
      <Enabled>true</Enabled>
      <!-- Un minuto de margen: al encender, la red tarda mas todavia. -->
      <Delay>PT1M</Delay>
    </BootTrigger>"""
        if usuario is not None:
            # S4U: «esta cuenta, sin sesion iniciada y sin guardar su contrasena».
            principal = f"""      <UserId>{escape(usuario)}</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>"""
        else:
            # S-1-5-18 es SYSTEM escrito de forma que no depende del idioma del
            # sistema: «NT AUTHORITY\SYSTEM» no se llama igual en todos.
            principal = """      <UserId>S-1-5-18</UserId>
      <RunLevel>LeastPrivilege</RunLevel>"""
    else:
        disparador = """    <LogonTrigger>
      <Enabled>true</Enabled>
      <!-- Un minuto de margen: al iniciar sesion la red aun no siempre esta. -->
      <Delay>PT1M</Delay>
    </LogonTrigger>"""
        principal = """      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>"""

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Scrappy: publica videos y memes en Telegram.</Description>
  </RegistrationInfo>
  <Triggers>
{disparador}
  </Triggers>
  <Principals>
    <Principal id="Author">
{principal}
    </Principal>
  </Principals>
  <Settings>
    <!-- Al iniciar sesion la red puede no estar lista todavia y el proceso
         termina con error. Sin esto no habria bot hasta el siguiente inicio
         de sesion; con esto Windows lo vuelve a intentar tres veces. -->
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <!-- Que no lo pare al desenchufar el portatil ni al entrar en ahorro. -->
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <!-- Sin limite de duracion: es un proceso de fondo, no una tarea que acaba. -->
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(interprete))}</Command>
      <Arguments>-m scrappy.cli run</Arguments>
      <WorkingDirectory>{escape(str(directorio))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


__all__ = [
    "NOMBRE_ACCESO",
    "NOMBRE_TAREA",
    "Autoarranque",
    "EstadoAutoarranque",
    "Modo",
]
