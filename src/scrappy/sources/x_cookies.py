"""Mantener viva la sesion de X sin ceremonia.

El backend `scrape` funciona con las cookies de una cuenta desechable, y esas
cookies se mueren. Cuando eso pase dentro de unos meses, nadie va a acordarse de
que existe un perfil de Firefox llamado `burner`, ni de que su carpeta se llama
`pTbhVY6z.Profile 1`, ni de que el fichero hay que filtrarlo antes de usarlo.
Este modulo es lo que evita tener que acordarse.

## Las dos cosas que hace y por que

**Traduce el nombre del perfil a su carpeta.** yt-dlp busca un directorio
literal, asi que `firefox:burner` falla con «could not find firefox cookies
database in ...\\Profiles\\burner»: el nombre que se ve en el gestor de perfiles
y el de la carpeta no son el mismo. La correspondencia esta en `profiles.ini`,
que es justo lo que se lee aqui.

**Filtra por dominio, y no es cosmetica.** `--cookies-from-browser` exporta el
perfil entero. En el caso real que motivo esto, el fichero traia 72 cookies:
las 17 de X y, entre el resto, la sesion de Outlook de la cuenta -es decir, el
buzon con el que se puede resetear la contrasena de X-. Ese fichero se le pasa a
yt-dlp en cada descarga. De aqui sale solo lo que X necesita.

## Lo que NO hace

No inicia sesion. Si el navegador tampoco la tiene, hay que entrar a X a mano:
Scrappy no maneja contrasenas de nadie.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path

from yt_dlp.cookies import YDLLogger, extract_cookies_from_browser

from scrappy.config.settings import Settings
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

#: De donde pueden salir las cookies utiles. Todo lo demas que traiga el perfil
#: -la sesion del correo, rastreadores publicitarios- se queda fuera.
DOMINIOS_DE_X = frozenset({"x.com", "twitter.com"})

#: Sin estas dos no hay sesion: `auth_token` es la sesion y `ct0` el csrf que
#: la GraphQL exige en cada peticion.
COOKIES_IMPRESCINDIBLES = ("auth_token", "ct0")

#: Donde Firefox lleva la cuenta de sus perfiles, por sistema operativo.
_PERFILES_INI = (
    Path.home() / "AppData/Roaming/Mozilla/Firefox/profiles.ini",
    Path.home() / ".mozilla/firefox/profiles.ini",
    Path.home() / "Library/Application Support/Firefox/profiles.ini",
)


@dataclass(frozen=True, slots=True)
class RenovacionCookies:
    """Como fue la renovacion, en un formato que sirve para las tres caras.

    `detalle` se pinta igual en la terminal, en un `notify` de la TUI y en una
    respuesta de Telegram: escribirlo tres veces solo serviria para que las tres
    dijeran cosas distintas.
    """

    ok: bool
    detalle: str
    total: int = 0
    de_x: int = 0
    faltan: tuple[str, ...] = ()
    ruta: Path | None = None


def resolver_perfil(navegador: str, perfil: str) -> str:
    """La carpeta del perfil, a partir del nombre que se ve en el navegador.

    Si `perfil` ya es una ruta que existe, se devuelve tal cual. Si no aparece
    en `profiles.ini` se devuelve como vino: puede que yt-dlp sepa apanarselo, y
    equivocarse aqui no debe impedir intentarlo.
    """
    if not perfil or navegador != "firefox":
        return perfil
    if Path(perfil).is_dir():
        return perfil

    for ini in _PERFILES_INI:
        if not ini.exists():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(ini, encoding="utf-8")
        except (OSError, configparser.Error) as exc:
            log.warning("profiles_ini_ilegible", ruta=str(ini), error=str(exc))
            continue

        for seccion in parser.sections():
            if parser.get(seccion, "Name", fallback=None) != perfil:
                continue
            ruta = parser.get(seccion, "Path", fallback="")
            if not ruta:
                continue
            # `IsRelative=1` -lo normal- significa relativo a profiles.ini.
            es_relativo = parser.get(seccion, "IsRelative", fallback="1") == "1"
            completa = (ini.parent / ruta) if es_relativo else Path(ruta)
            log.debug("perfil_resuelto", nombre=perfil, ruta=str(completa))
            return str(completa)

    return perfil


def _partir(especificacion: str) -> tuple[str, str]:
    """`firefox:burner` -> `("firefox", "burner")`. Sin perfil, cadena vacia."""
    navegador, _, perfil = especificacion.strip().partition(":")
    return navegador.strip().lower(), perfil.strip()


def solo_de_x(tarro: CookieJar) -> list[Cookie]:
    """Las cookies de X de un tarro cualquiera. Ver la cabecera del modulo."""
    return [c for c in tarro if c.domain.lstrip(".") in DOMINIOS_DE_X]


def renovar_cookies(settings: Settings) -> RenovacionCookies:
    """Reextrae las cookies de X del navegador y las deja en su fichero.

    No levanta nunca: esto se llama desde un boton de Telegram, desde la TUI y
    desde una ronda automatica, y en los tres sitios un fallo tiene que ser un
    mensaje, no un traceback.
    """
    destino = settings.x_cookies_file
    if destino is None:
        return RenovacionCookies(
            False,
            "Falta SCRAPPY_X_COOKIES_FILE: no se sabe donde dejarlas.",
        )
    if not settings.x_cookies_browser.strip():
        return RenovacionCookies(
            False,
            "Falta SCRAPPY_X_COOKIES_BROWSER. Ponlo a `firefox:burner` -el "
            "navegador y el perfil donde tengas la sesion de la cuenta "
            "desechable- y esto se renovara solo.",
            ruta=destino,
        )

    navegador, perfil = _partir(settings.x_cookies_browser)
    try:
        # Para Firefox, yt-dlp copia la base a un temporal antes de leerla, asi
        # que esto funciona con el navegador abierto. Comprobado.
        tarro = extract_cookies_from_browser(
            navegador, resolver_perfil(navegador, perfil) or None, YDLLogger()
        )
    except Exception as exc:  # yt-dlp lanza de todo: ValueError, OSError, suyas
        return RenovacionCookies(
            False,
            f"No se pudieron leer las cookies de {navegador}: {exc}",
            ruta=destino,
        )

    de_x = solo_de_x(tarro)
    nombres = {c.name for c in de_x}
    faltan = tuple(n for n in COOKIES_IMPRESCINDIBLES if n not in nombres)
    if faltan:
        # Escribir un fichero sin sesion seria cambiar unas cookies muertas por
        # otras y perder las que habia.
        return RenovacionCookies(
            False,
            f"En {navegador}"
            + (f" (perfil {perfil})" if perfil else "")
            + f" no hay sesion de X: faltan {' y '.join(faltan)}. "
            "Abre ese perfil, entra en x.com con la cuenta desechable, y repite.",
            total=len(list(tarro)),
            de_x=len(de_x),
            faltan=faltan,
            ruta=destino,
        )

    nuevo = MozillaCookieJar(str(destino))
    for cookie in de_x:
        nuevo.set_cookie(cookie)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        nuevo.save(ignore_discard=True, ignore_expires=True)
    except OSError as exc:
        return RenovacionCookies(False, f"No se pudo escribir {destino}: {exc}", ruta=destino)

    total = len(list(tarro))
    log.info("cookies_renovadas", navegador=navegador, perfil=perfil, de_x=len(de_x))
    return RenovacionCookies(
        True,
        f"Sesion de X renovada: {len(de_x)} cookies guardadas "
        f"(de {total} del perfil; el resto no es de X y se descarta).",
        total=total,
        de_x=len(de_x),
        ruta=destino,
    )


def cookies_del_fichero(settings: Settings) -> dict[str, str]:
    """Las cookies de X del fichero configurado, o {} si no hay nada usable.

    Vive aqui y no en el adapter porque la usan tambien el diagnostico y la
    validacion de cuentas, y las tres tienen que filtrar por dominio igual.
    """
    from http.cookiejar import LoadError

    ruta = settings.x_cookies_file
    if ruta is None or not ruta.exists():
        return {}

    tarro = MozillaCookieJar(str(ruta))
    try:
        tarro.load(ignore_discard=True, ignore_expires=True)
    except (OSError, LoadError) as exc:
        log.warning("cookies_ilegibles", error=str(exc))
        return {}
    return {c.name: c.value or "" for c in solo_de_x(tarro)}


__all__ = [
    "COOKIES_IMPRESCINDIBLES",
    "DOMINIOS_DE_X",
    "RenovacionCookies",
    "cookies_del_fichero",
    "renovar_cookies",
    "resolver_perfil",
    "solo_de_x",
]
