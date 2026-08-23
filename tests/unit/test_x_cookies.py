"""Pruebas de la renovacion de la sesion de X.

Las dos cosas que este modulo tiene que hacer bien salieron de fallos reales al
montarlo, no de imaginar lo que podria salir mal:

  1. **El nombre del perfil no es el de su carpeta.** `firefox:burner` fallaba
     con «could not find firefox cookies database in ...\\Profiles\\burner»,
     porque la carpeta se llamaba `pTbhVY6z.Profile 1`.
  2. **El fichero exportado trae mucho mas que X.** En el caso real venia con la
     sesion de Outlook de la cuenta -el buzon con el que se resetea la
     contrasena de X- y dos docenas de rastreadores.
"""

from __future__ import annotations

import http.cookiejar as cj
from pathlib import Path
from typing import Any

import pytest

from scrappy.config.settings import Settings
from scrappy.sources import x_cookies
from scrappy.sources.x_cookies import cookies_del_fichero, renovar_cookies, resolver_perfil


def _cookie(nombre: str, dominio: str) -> cj.Cookie:
    return cj.Cookie(
        version=0,
        name=nombre,
        value=f"valor-de-{nombre}",
        port=None,
        port_specified=False,
        domain=dominio,
        domain_specified=True,
        domain_initial_dot=dominio.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )


def _tarro(*cookies: cj.Cookie) -> cj.CookieJar:
    tarro = cj.CookieJar()
    for cookie in cookies:
        tarro.set_cookie(cookie)
    return tarro


#: Lo que devolveria un perfil de navegador real: la sesion de X, la del correo
#: con el que se registro, y basura publicitaria.
PERFIL_COMPLETO = (
    _cookie("auth_token", ".x.com"),
    _cookie("ct0", ".x.com"),
    _cookie("guest_id", ".twitter.com"),
    _cookie("RPSSecAuth", ".outlook.live.com"),
    _cookie("IDE", ".doubleclick.net"),
)


@pytest.fixture
def settings_de_x(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "x_enabled": True,
            "x_cookies_file": tmp_path / "sub" / "carpeta" / "x.cookies.txt",
            "x_cookies_browser": "firefox:burner",
        }
    )


# ---------------------------------------------------------------------------
# El nombre del perfil
# ---------------------------------------------------------------------------
def test_el_nombre_del_perfil_se_traduce_a_su_carpeta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El fallo que costo una ronda de depuracion: no son lo mismo."""
    ini = tmp_path / "profiles.ini"
    ini.write_text(
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abc.default-release\n\n"
        "[Profile1]\nName=burner\nIsRelative=1\nPath=Profiles/pTbhVY6z.Profile 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(x_cookies, "_PERFILES_INI", (ini,))

    resuelto = resolver_perfil("firefox", "burner")

    assert resuelto.endswith("pTbhVY6z.Profile 1")
    assert Path(resuelto).parent == tmp_path / "Profiles"


def test_un_perfil_desconocido_se_deja_como_vino(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equivocarse aqui no debe impedir intentarlo: yt-dlp puede apanarselo."""
    ini = tmp_path / "profiles.ini"
    ini.write_text("[Profile0]\nName=otro\nPath=Profiles/x\n", encoding="utf-8")
    monkeypatch.setattr(x_cookies, "_PERFILES_INI", (ini,))

    assert resolver_perfil("firefox", "burner") == "burner"


def test_una_ruta_que_existe_no_se_toca(tmp_path: Path) -> None:
    assert resolver_perfil("firefox", str(tmp_path)) == str(tmp_path)


def test_los_navegadores_chromium_no_pasan_por_profiles_ini() -> None:
    """`profiles.ini` es cosa de Firefox; en Edge el perfil ya es su carpeta."""
    assert resolver_perfil("edge", "Default") == "Default"


# ---------------------------------------------------------------------------
# La renovacion
# ---------------------------------------------------------------------------
def test_del_perfil_solo_sale_lo_de_x(
    settings_de_x: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo mas importante de este modulo.

    El fichero se le pasa a yt-dlp en cada descarga; que ahi viaje la sesion del
    correo de la cuenta convierte un fichero de cookies en un problema serio.
    """
    monkeypatch.setattr(
        x_cookies, "extract_cookies_from_browser", lambda *_a, **_k: _tarro(*PERFIL_COMPLETO)
    )

    resultado = renovar_cookies(settings_de_x)

    assert resultado.ok
    assert resultado.de_x == 3
    assert resultado.total == 5

    guardadas = cookies_del_fichero(settings_de_x)
    assert set(guardadas) == {"auth_token", "ct0", "guest_id"}
    assert "RPSSecAuth" not in guardadas


def test_se_crea_el_directorio_destino(
    settings_de_x: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paso de verdad: el comando fallaba con FileNotFoundError por esto."""
    monkeypatch.setattr(
        x_cookies, "extract_cookies_from_browser", lambda *_a, **_k: _tarro(*PERFIL_COMPLETO)
    )
    assert settings_de_x.x_cookies_file is not None
    assert not settings_de_x.x_cookies_file.parent.exists()

    assert renovar_cookies(settings_de_x).ok
    assert settings_de_x.x_cookies_file.exists()


def test_sin_sesion_en_el_navegador_no_se_pisa_lo_que_habia(
    settings_de_x: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cambiar unas cookies muertas por otras seria empeorar las cosas."""
    assert settings_de_x.x_cookies_file is not None
    settings_de_x.x_cookies_file.parent.mkdir(parents=True)
    settings_de_x.x_cookies_file.write_text("# lo que ya habia\n", encoding="utf-8")

    monkeypatch.setattr(
        x_cookies,
        "extract_cookies_from_browser",
        lambda *_a, **_k: _tarro(_cookie("guest_id", ".x.com")),
    )

    resultado = renovar_cookies(settings_de_x)

    assert not resultado.ok
    assert "auth_token" in resultado.detalle and "ct0" in resultado.detalle
    assert settings_de_x.x_cookies_file.read_text(encoding="utf-8") == "# lo que ya habia\n"


def test_sin_navegador_configurado_se_dice_como_ponerlo(settings_de_x: Settings) -> None:
    sin_navegador = settings_de_x.model_copy(update={"x_cookies_browser": ""})

    resultado = renovar_cookies(sin_navegador)

    assert not resultado.ok
    assert "SCRAPPY_X_COOKIES_BROWSER" in resultado.detalle


def test_si_el_navegador_falla_se_explica(
    settings_de_x: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yt-dlp lanza de todo aqui, y nada de eso puede llegar como traceback."""

    def _revienta(*_a: Any, **_k: Any) -> Any:
        raise ValueError("no se pudo descifrar la base de datos")

    monkeypatch.setattr(x_cookies, "extract_cookies_from_browser", _revienta)

    resultado = renovar_cookies(settings_de_x)

    assert not resultado.ok
    assert "descifrar" in resultado.detalle
