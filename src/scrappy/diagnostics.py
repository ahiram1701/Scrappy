"""Comprobaciones de configuracion, en un solo sitio.

`scrappy doctor`, el `/start` del bot y el asistente de la TUI hacen exactamente
las mismas preguntas: ¿el token vale?, ¿el bot alcanza el chat?, ¿hay ffmpeg?
Escritas tres veces se desincronizarian a la primera, asi que viven aqui y cada
consumidor solo las pinta a su manera.

## Por que estas comprobaciones y no otras

Salen de fallos reales al configurar el proyecto por primera vez, no de
imaginar lo que podria salir mal:

- El token quedo como `123456789:` (el valor de ejemplo de la plantilla) pegado
  delante del token real, porque al pegarlo no se sustituyo la linea entera.
  Telegram respondia «token rejected» sin mas pista.
- El chat id se puso negativo copiando la forma del ejemplo, que era un canal.
  Para un chat privado va positivo, y Telegram respondia «Chat not found».

Los dos costaron varias rondas de depuracion leyendo tracebacks. Cada
comprobacion lleva su linea de **como arreglarlo**, que es lo que faltaba.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from enum import StrEnum

from scrappy.config.loader import load_sources_config
from scrappy.config.settings import Settings
from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# Un token de Telegram es `<id del bot>:<secreto>`. Un solo `:`, y el secreto
# no lleva ni espacios ni dos puntos. Basta para cazar el caso del prefijo
# duplicado, que produce dos bloques numericos separados por dos puntos.
_TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,}$")

_CHAT_ID_RE = re.compile(r"^-?\d+$")


class CheckStatus(StrEnum):
    """Gravedad de una comprobacion."""

    OK = "ok"
    #: Funciona, pero hay algo que conviene mirar.
    WARNING = "warning"
    #: No funciona. Bloquea el uso normal.
    ERROR = "error"
    #: No aplica con la configuracion actual.
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Check:
    """Resultado de una comprobacion.

    `fix` es la parte que importa: decir que fallo sin decir que hacer es lo
    que convirtio dos erratas en varias rondas de depuracion.
    """

    name: str
    status: CheckStatus
    detail: str
    fix: str = ""

    @property
    def blocking(self) -> bool:
        return self.status is CheckStatus.ERROR

    def render(self) -> str:
        """Una linea por comprobacion, valida para terminal y para Telegram."""
        marca = {
            CheckStatus.OK: "OK",
            CheckStatus.WARNING: "AVISO",
            CheckStatus.ERROR: "FALLO",
            CheckStatus.SKIPPED: "--",
        }[self.status]
        linea = f"[{marca}] {self.name}: {self.detail}"
        if self.fix and self.status is not CheckStatus.OK:
            linea += f"\n        -> {self.fix}"
        return linea


@dataclass(slots=True)
class Diagnosis:
    """Todas las comprobaciones de una pasada."""

    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True si nada bloquea. Los avisos no cuentan."""
        return not any(check.blocking for check in self.checks)

    @property
    def blocking(self) -> list[Check]:
        return [check for check in self.checks if check.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status is CheckStatus.WARNING]

    def render(self) -> str:
        return "\n".join(check.render() for check in self.checks)

    def summary(self) -> str:
        """Una linea de resumen para barras de estado y titulares."""
        if self.blocking:
            return f"{len(self.blocking)} problema(s) que impiden funcionar"
        if self.warnings:
            return f"Funciona, con {len(self.warnings)} aviso(s)"
        return "Todo correcto"


# ---------------------------------------------------------------------------
# Comprobaciones puras (sin red: faciles de probar)
# ---------------------------------------------------------------------------
def check_token_format(token: str) -> Check:
    """Formato del token, antes de gastar una llamada a Telegram."""
    nombre = "Token del bot"

    if not token:
        return Check(
            nombre,
            CheckStatus.ERROR,
            "sin configurar",
            "Habla con @BotFather, usa /newbot y pon el token en SCRAPPY_TELEGRAM_BOT_TOKEN",
        )

    if token.count(":") > 1:
        # El caso real: `123456789:` de la plantilla pegado delante del token.
        return Check(
            nombre,
            CheckStatus.ERROR,
            "tiene mas de un ':', asi que hay dos tokens pegados",
            "Seguramente quedo el valor de ejemplo delante del tuyo. Deja solo "
            "el que te dio @BotFather, con un unico ':'",
        )

    if not _TOKEN_RE.match(token):
        return Check(
            nombre,
            CheckStatus.ERROR,
            "no tiene el formato de un token de Telegram",
            "Debe ser <numero>:<letras y numeros>, tal cual lo da @BotFather",
        )

    return Check(nombre, CheckStatus.OK, "formato correcto")


def check_chat_id_format(chat_id: str) -> Check:
    """Forma del chat destino.

    El signo es el error mas facil de cometer, porque la plantilla trae un
    ejemplo de canal (negativo) y mucha gente empieza mandandoselo a si misma.
    """
    nombre = "Chat destino"

    if not chat_id:
        return Check(
            nombre,
            CheckStatus.ERROR,
            "sin configurar",
            "Pon tu id de usuario (positivo) para recibirlo en privado, o el "
            "del canal (empieza por -100). Tu id te lo dice @userinfobot",
        )

    if not _CHAT_ID_RE.match(chat_id):
        return Check(
            nombre,
            CheckStatus.ERROR,
            f"«{chat_id}» no es un id numerico",
            "Debe ser solo numeros, con un '-' delante si es canal o grupo",
        )

    if chat_id.startswith("-") and not chat_id.startswith("-100"):
        # El caso real: id de usuario con un menos delante.
        return Check(
            nombre,
            CheckStatus.WARNING,
            "es negativo pero no empieza por -100",
            "Si querias tu chat privado, quita el '-': ahi el id va POSITIVO. "
            "Los canales y supergrupos si empiezan por -100",
        )

    if chat_id.startswith("-100"):
        return Check(nombre, CheckStatus.OK, "formato de canal o supergrupo")
    return Check(nombre, CheckStatus.OK, "formato de chat privado")


def check_admin_ids(settings: Settings) -> Check:
    """Sin administradores nadie puede usar los comandos del bot."""
    nombre = "Administradores"
    try:
        admins = settings.admin_ids
    except ConfigError as exc:
        return Check(
            nombre,
            CheckStatus.ERROR,
            str(exc),
            "SCRAPPY_TELEGRAM_ADMIN_IDS admite varios ids separados por coma",
        )

    if not admins:
        return Check(
            nombre,
            CheckStatus.WARNING,
            "vacio: nadie podra usar los comandos",
            "Pon tu id en SCRAPPY_TELEGRAM_ADMIN_IDS. Te lo dice @userinfobot. "
            "El scheduler sigue publicando aunque este vacio",
        )
    return Check(nombre, CheckStatus.OK, f"{len(admins)} autorizado(s)")


def check_ffmpeg() -> Check:
    """Sin ffmpeg los videos fallan todos, y en silencio."""
    nombre = "ffmpeg"
    if shutil.which("ffmpeg") is None:
        return Check(
            nombre,
            CheckStatus.ERROR,
            "no esta en el PATH: los videos fallaran",
            "Windows: winget install Gyan.FFmpeg  ·  macOS: brew install ffmpeg  "
            "·  Debian: sudo apt install ffmpeg. Cierra y reabre la terminal despues",
        )
    return Check(nombre, CheckStatus.OK, "disponible")


def check_reddit_user_agent(settings: Settings) -> Check:
    """Reddit responde 429 a los User-Agent genericos."""
    nombre = "User-Agent de Reddit"
    if not settings.reddit_enabled:
        return Check(nombre, CheckStatus.SKIPPED, "Reddit desactivado")

    if "/u/" not in settings.reddit_user_agent:
        return Check(
            nombre,
            CheckStatus.WARNING,
            "no te identifica, y Reddit penaliza eso con 429",
            "Usa el formato 'windows:scrappy:0.1.0 (by /u/tu_usuario)' con tu "
            "usuario real de Reddit",
        )
    return Check(nombre, CheckStatus.OK, "identifica correctamente")


def check_sources_config(settings: Settings) -> Check:
    """El catalogo de fuentes existe y cumple el esquema."""
    nombre = "config/sources.yaml"
    path = settings.sources_config_path

    if not path.exists():
        return Check(
            nombre,
            CheckStatus.WARNING,
            f"{path} no existe; se usan los valores por defecto",
            "Copia config/sources.example.yaml a config/sources.yaml para "
            "elegir subreddits, comunidades y consultas",
        )

    try:
        load_sources_config(path)
    except ConfigError as exc:
        return Check(
            nombre,
            CheckStatus.ERROR,
            str(exc).splitlines()[0],
            "Corrige el fichero, o parte de nuevo desde sources.example.yaml",
        )
    return Check(nombre, CheckStatus.OK, "valido")


def check_enabled_sources(settings: Settings) -> Check:
    """Al menos una fuente tiene que estar activa o no habra nada que publicar."""
    nombre = "Fuentes activas"
    activas = settings.enabled_source_names()

    if not activas:
        return Check(
            nombre,
            CheckStatus.ERROR,
            "ninguna fuente activada",
            "Reddit, Lemmy y Bluesky funcionan sin credenciales: activa alguna "
            "con SCRAPPY_REDDIT_ENABLED=true (o LEMMY, o BLUESKY)",
        )
    return Check(nombre, CheckStatus.OK, ", ".join(activas))


def check_x_cookies(settings: Settings) -> Check:
    """La sesion de X, que es lo unico de esa fuente que caduca solo.

    No se mira la fecha de caducidad a proposito. Se comprobo contra el fichero
    real: `auth_token` caduca dentro de **un ano**, asi que un aviso por fecha
    no llegaria nunca a tiempo de nada. Lo que mata la sesion es que X la
    invalide, y eso solo se ve pidiendole algo (ver `check_x_sesion`).
    """
    from scrappy.config.settings import XBackend
    from scrappy.sources.x_cookies import COOKIES_IMPRESCINDIBLES, cookies_del_fichero

    nombre = "Sesion de X"
    if not settings.x_enabled:
        return Check(nombre, CheckStatus.SKIPPED, "X desactivada")
    if settings.x_backend is not XBackend.SCRAPE:
        return Check(nombre, CheckStatus.SKIPPED, "backend `api`: no usa cookies")

    arreglo = (
        "ejecuta `scrappy cookies` (o el boton «Renovar cookies» de /sources). "
        "Necesita SCRAPPY_X_COOKIES_BROWSER, por ejemplo `firefox:burner`"
    )

    ruta = settings.x_cookies_file
    if ruta is None:
        return Check(nombre, CheckStatus.ERROR, "falta SCRAPPY_X_COOKIES_FILE", arreglo)
    if not ruta.exists():
        return Check(nombre, CheckStatus.ERROR, f"no existe {ruta}", arreglo)

    cookies = cookies_del_fichero(settings)
    faltan = [n for n in COOKIES_IMPRESCINDIBLES if not cookies.get(n)]
    if faltan:
        return Check(
            nombre,
            CheckStatus.ERROR,
            f"el fichero no trae {' ni '.join(faltan)}",
            arreglo,
        )

    if not settings.x_cookies_browser.strip():
        # No impide funcionar hoy, pero el dia que la sesion muera habra que
        # hacerlo todo a mano. Avisar ahora cuesta una linea.
        return Check(
            nombre,
            CheckStatus.WARNING,
            "la sesion esta, pero no se puede renovar sola",
            "pon SCRAPPY_X_COOKIES_BROWSER (por ejemplo `firefox:burner`) y "
            "Scrappy la renovara solo cuando caduque",
        )
    return Check(nombre, CheckStatus.OK, "sesion completa, se renueva sola al caducar")


def check_x_ritmo(settings: Settings) -> Check:
    """A que ritmo se le pide a X, comparado con lo que X permite.

    Existe porque el numero por defecto se subio. Un `objetivos_por_ronda` alto
    combinado con un intervalo corto multiplica las peticiones sin que se note
    en ninguna de las dos pantallas donde se configuran, que estan separadas.
    Aqui se ven juntas, que es la unica forma de juzgarlo.
    """
    from scrappy.config.settings import XBackend

    nombre = "Ritmo de X"
    if not settings.x_enabled or settings.x_backend is not XBackend.SCRAPE:
        return Check(nombre, CheckStatus.SKIPPED, "no aplica")

    from scrappy.sources.x import TOPE_POR_VENTANA, VENTANA_MINUTOS, ritmo

    try:
        catalogo = load_sources_config(settings.sources_config_path)
        config = catalogo.for_source("x")
    except ConfigError:
        # Ya lo dice `check_sources_config`; repetirlo aqui seria ruido.
        return Check(nombre, CheckStatus.SKIPPED, "no se pudo leer el catalogo")

    cuentas = len(config.get_list("accounts")) or 1
    por_ronda = min(config.get_int("objetivos_por_ronda", cuentas), cuentas)
    al_dia, por_ventana = ritmo(por_ronda, settings.schedule_interval_minutes)
    detalle = (
        f"{al_dia} perfiles al dia, {por_ventana} peticiones por ventana "
        f"de {VENTANA_MINUTOS} min (tope {TOPE_POR_VENTANA})"
    )

    if por_ventana > TOPE_POR_VENTANA:
        return Check(
            nombre,
            CheckStatus.ERROR,
            detalle,
            "baja `objetivos_por_ronda` en sources.yaml o sube el intervalo del "
            "scheduler: asi X va a responder 429",
        )
    if por_ventana * 5 > TOPE_POR_VENTANA:
        return Check(
            nombre,
            CheckStatus.WARNING,
            detalle,
            "vas por encima del 20% de lo que X permite. Funciona, pero es mas "
            "trafico del que justifica una fuente de memes",
        )
    return Check(nombre, CheckStatus.OK, detalle)


async def check_x_sesion(settings: Settings) -> Check:
    """Si la sesion de X sirve de verdad. Una peticion, no mas.

    Es la unica forma honesta de saberlo: un fichero con las cookies correctas
    puede estar muerto desde ayer si X las revoco.
    """
    from scrappy.config.settings import XBackend

    nombre = "Sesion de X (en vivo)"
    if not settings.x_enabled or settings.x_backend is not XBackend.SCRAPE:
        return Check(nombre, CheckStatus.SKIPPED, "no aplica")

    from scrappy.config.loader import SourceConfig
    from scrappy.core.errors import SourceError
    from scrappy.sources.registry import build_http_client
    from scrappy.sources.x import SesionInvalidaError, XScrapeSource

    async with build_http_client() as client:
        fuente = XScrapeSource(settings, SourceConfig(), client)
        cookies = fuente._cookies()
        if not cookies:
            return Check(nombre, CheckStatus.SKIPPED, "sin cookies que probar")
        try:
            queries = await fuente._query_ids(cookies)
            await fuente._user_id("x", cookies, queries)
        except SesionInvalidaError as exc:
            return Check(
                nombre,
                CheckStatus.ERROR,
                str(exc),
                "renuevala con `scrappy cookies` o el boton de /sources",
            )
        except SourceError as exc:
            # Que X este caida o cambie el bundle no es culpa de la sesion.
            return Check(nombre, CheckStatus.WARNING, f"no se pudo comprobar: {exc}")

    return Check(nombre, CheckStatus.OK, "la sesion responde")


# ---------------------------------------------------------------------------
# Comprobaciones que hablan con Telegram
# ---------------------------------------------------------------------------
async def check_telegram(settings: Settings) -> list[Check]:
    """Comprueba contra Telegram que el bot existe y alcanza el chat.

    Solo se ejecuta si el formato ya era correcto: gastar una llamada para que
    la rechacen por una errata evidente no aporta nada.
    """
    from telegram import Bot
    from telegram.error import TelegramError

    token = settings.telegram_bot_token.get_secret_value()
    chat_id = settings.telegram_target_chat_id

    checks: list[Check] = []
    bot = Bot(token)

    try:
        async with bot:
            try:
                me = await bot.get_me()
                checks.append(
                    Check("Bot en Telegram", CheckStatus.OK, f"@{me.username} (id {me.id})")
                )
            except TelegramError as exc:
                checks.append(
                    Check(
                        "Bot en Telegram",
                        CheckStatus.ERROR,
                        f"Telegram rechazo el token: {exc}",
                        "Comprueba el token con @BotFather; si lo revocaste, "
                        "genera uno nuevo con /token",
                    )
                )
                return checks

            if not chat_id:
                checks.append(Check("Acceso al chat", CheckStatus.SKIPPED, "sin chat destino"))
                return checks

            try:
                chat = await bot.get_chat(chat_id)
                nombre = chat.title or chat.full_name or str(chat.id)
                checks.append(
                    Check(
                        "Acceso al chat",
                        CheckStatus.OK,
                        f"{nombre} (tipo {chat.type})",
                    )
                )
            except TelegramError as exc:
                checks.append(
                    Check(
                        "Acceso al chat",
                        CheckStatus.ERROR,
                        f"no se puede acceder al chat {chat_id}: {exc}",
                        _pista_de_chat(chat_id),
                    )
                )
    except TelegramError as exc:  # fallo al inicializar el bot
        checks.append(
            Check(
                "Bot en Telegram",
                CheckStatus.ERROR,
                f"no se pudo contactar con Telegram: {exc}",
                "Revisa tu conexion y el token",
            )
        )

    return checks


def _pista_de_chat(chat_id: str) -> str:
    """La causa mas probable depende de la forma del id."""
    if chat_id.startswith("-100"):
        return "Anade el bot al canal como administrador con permiso para publicar"
    if chat_id.startswith("-"):
        return "Ese id es negativo sin ser -100. Si querias tu chat privado, quitale el '-'"
    return (
        "Abre tu bot en Telegram y pulsa Start: Telegram no deja que un bot "
        "escriba primero a un usuario"
    )


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------
async def run_diagnostics(settings: Settings, *, use_network: bool = True) -> Diagnosis:
    """Ejecuta todas las comprobaciones.

    Args:
        use_network: si False, se salta las que hablan con Telegram. Lo usan
            los tests y la TUI cuando solo quiere refrescar lo barato.
    """
    token = settings.telegram_bot_token.get_secret_value()

    checks = [
        check_token_format(token),
        check_chat_id_format(settings.telegram_target_chat_id),
        check_admin_ids(settings),
        check_ffmpeg(),
        check_enabled_sources(settings),
        check_reddit_user_agent(settings),
        check_sources_config(settings),
        check_x_cookies(settings),
        check_x_ritmo(settings),
    ]

    # Solo se llama a Telegram si el formato ya era correcto: si el token tiene
    # una errata evidente, la llamada solo anadiria un error redundante.
    formato_ok = all(check.status is not CheckStatus.ERROR for check in checks[:2])
    if use_network:
        checks.append(await check_x_sesion(settings))

    if use_network and formato_ok and token:
        checks.extend(await check_telegram(settings))
    elif use_network and not formato_ok:
        checks.append(
            Check(
                "Acceso a Telegram",
                CheckStatus.SKIPPED,
                "no se comprueba hasta que el token y el chat tengan buen formato",
            )
        )

    return Diagnosis(checks=checks)
