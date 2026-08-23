"""Pruebas del diagnostico.

Los casos importantes no son inventados: reproducen los dos fallos que
costaron varias rondas de depuracion al configurar el bot por primera vez, y
que ninguna herramienta detectaba.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from scrappy.config.settings import Settings
from scrappy.diagnostics import (
    CheckStatus,
    check_admin_ids,
    check_chat_id_format,
    check_enabled_sources,
    check_reddit_user_agent,
    check_sources_config,
    check_token_format,
    run_diagnostics,
)

TOKEN_VALIDO = "8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU"


# ---------------------------------------------------------------------------
# El caso real 1: prefijo de la plantilla pegado delante del token
# ---------------------------------------------------------------------------
def test_caza_el_token_con_prefijo_duplicado() -> None:
    """El fallo real: al pegar el token no se sustituyo la linea entera.

    Quedo `123456789:` (el valor de ejemplo) delante del token bueno, y
    Telegram solo decia «token rejected».
    """
    check = check_token_format(f"123456789:{TOKEN_VALIDO}")

    assert check.status is CheckStatus.ERROR
    assert "dos tokens pegados" in check.detail
    # Y dice como arreglarlo, que es lo que faltaba.
    assert "valor de ejemplo" in check.fix


def test_acepta_un_token_bien_formado() -> None:
    assert check_token_format(TOKEN_VALIDO).status is CheckStatus.OK


def test_token_vacio() -> None:
    check = check_token_format("")
    assert check.status is CheckStatus.ERROR
    assert "BotFather" in check.fix


def test_token_con_basura() -> None:
    assert check_token_format("esto-no-es-un-token").status is CheckStatus.ERROR


# ---------------------------------------------------------------------------
# El caso real 2: id de usuario con un menos delante
# ---------------------------------------------------------------------------
def test_caza_el_chat_id_con_el_signo_cambiado() -> None:
    """El fallo real: id de usuario copiado con el menos del ejemplo de canal.

    Telegram respondia «Chat not found» sin explicar que buscaba un grupo.
    """
    check = check_chat_id_format("-1412545148")

    assert check.status is CheckStatus.WARNING
    assert "-100" in check.detail
    assert "POSITIVO" in check.fix


@pytest.mark.parametrize(
    ("chat_id", "esperado"),
    [
        ("1412545148", "privado"),
        ("-1001234567890", "canal"),
    ],
)
def test_acepta_los_dos_formatos_validos(chat_id: str, esperado: str) -> None:
    check = check_chat_id_format(chat_id)
    assert check.status is CheckStatus.OK
    assert esperado in check.detail


def test_chat_id_vacio_o_no_numerico() -> None:
    assert check_chat_id_format("").status is CheckStatus.ERROR
    assert check_chat_id_format("@micanal").status is CheckStatus.ERROR


# ---------------------------------------------------------------------------
# Resto de comprobaciones
# ---------------------------------------------------------------------------
def test_avisa_si_no_hay_administradores() -> None:
    check = check_admin_ids(Settings(telegram_admin_ids=""))
    assert check.status is CheckStatus.WARNING
    assert "userinfobot" in check.fix


def test_admin_ids_no_numericos_es_error() -> None:
    check = check_admin_ids(Settings(telegram_admin_ids="1,pepe"))
    assert check.status is CheckStatus.ERROR


def test_admin_ids_correctos() -> None:
    check = check_admin_ids(Settings(telegram_admin_ids="1,2"))
    assert check.status is CheckStatus.OK
    assert "2 autorizado" in check.detail


def test_avisa_del_user_agent_generico_de_reddit() -> None:
    """Es la causa de los 429 constantes."""
    check = check_reddit_user_agent(
        Settings(reddit_enabled=True, reddit_user_agent="python-requests/2.0")
    )
    assert check.status is CheckStatus.WARNING
    assert "429" in check.detail


def test_no_comprueba_el_user_agent_si_reddit_esta_apagado() -> None:
    check = check_reddit_user_agent(Settings(reddit_enabled=False))
    assert check.status is CheckStatus.SKIPPED


def test_exige_alguna_fuente_activa() -> None:
    apagadas = Settings(
        reddit_enabled=False,
        lemmy_enabled=False,
        bluesky_enabled=False,
    )
    check = check_enabled_sources(apagadas)
    assert check.status is CheckStatus.ERROR
    # Sugiere las que no piden credenciales.
    assert "sin credenciales" in check.fix


def test_sources_yaml_ausente_es_solo_aviso(tmp_path: Path) -> None:
    """Sin el fichero se usan los valores por defecto: molesto, no fatal."""
    check = check_sources_config(Settings(sources_config_path=tmp_path / "no.yaml"))
    assert check.status is CheckStatus.WARNING
    assert "sources.example.yaml" in check.fix


def test_sources_yaml_invalido_es_error(tmp_path: Path) -> None:
    roto = tmp_path / "sources.yaml"
    roto.write_text("ranking: [sin cerrar", encoding="utf-8")

    check = check_sources_config(Settings(sources_config_path=roto))
    assert check.status is CheckStatus.ERROR


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------
async def test_no_llama_a_telegram_si_el_formato_ya_esta_mal(tmp_path: Path) -> None:
    """Gastar una llamada para que la rechacen por una errata evidente no aporta."""
    settings = Settings(
        telegram_bot_token=SecretStr("123456789:" + TOKEN_VALIDO),
        telegram_target_chat_id="-1412545148",
        sources_config_path=tmp_path / "no.yaml",
    )

    diagnosis = await run_diagnostics(settings, use_network=True)

    saltada = next(c for c in diagnosis.checks if c.name == "Acceso a Telegram")
    assert saltada.status is CheckStatus.SKIPPED
    assert not diagnosis.ok


async def test_sin_red_no_toca_telegram(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token=SecretStr(TOKEN_VALIDO),
        telegram_target_chat_id="1412545148",
        telegram_admin_ids="1412545148",
        sources_config_path=tmp_path / "no.yaml",
    )

    diagnosis = await run_diagnostics(settings, use_network=False)

    assert not any("Telegram" in c.name for c in diagnosis.checks)


async def test_el_resumen_distingue_bloqueo_de_aviso(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token=SecretStr(TOKEN_VALIDO),
        telegram_target_chat_id="1412545148",
        telegram_admin_ids="",  # solo un aviso
        sources_config_path=tmp_path / "no.yaml",
    )

    diagnosis = await run_diagnostics(settings, use_network=False)

    assert diagnosis.warnings
    # Los avisos no impiden funcionar.
    assert diagnosis.ok is (not diagnosis.blocking)


def test_el_render_incluye_el_arreglo_solo_cuando_hace_falta() -> None:
    fallo = check_token_format("")
    bien = check_token_format(TOKEN_VALIDO)

    assert "->" in fallo.render()
    assert "->" not in bien.render()


# ---------------------------------------------------------------------------
# La sesion de X
#
# Escrita una vez aqui y pintada por `scrappy doctor`, el /start del bot y la
# TUI. Es la unica credencial del proyecto que caduca sola.
# ---------------------------------------------------------------------------
def _con_x(settings: Settings, tmp_path: Path, **extra: object) -> Settings:
    from scrappy.config.settings import XBackend

    base: dict[str, object] = {"x_enabled": True, "x_backend": XBackend.SCRAPE}
    base.update(extra)
    return settings.model_copy(update=base)


def _fichero_de_cookies(tmp_path: Path, *nombres: str) -> Path:
    ruta = tmp_path / "x.cookies.txt"
    lineas = ["# Netscape HTTP Cookie File"]
    lineas += [f".x.com\tTRUE\t/\tTRUE\t0\t{n}\tvalor" for n in nombres]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def test_x_apagada_no_se_comprueba(settings: Settings) -> None:
    from scrappy.diagnostics import check_x_cookies

    assert check_x_cookies(settings).status is CheckStatus.SKIPPED


def test_el_backend_api_no_usa_cookies(settings: Settings) -> None:
    from scrappy.config.settings import XBackend
    from scrappy.diagnostics import check_x_cookies

    con_api = settings.model_copy(update={"x_enabled": True, "x_backend": XBackend.API})

    assert check_x_cookies(con_api).status is CheckStatus.SKIPPED


def test_sin_fichero_de_cookies_es_un_fallo(settings: Settings, tmp_path: Path) -> None:
    from scrappy.diagnostics import check_x_cookies

    check = check_x_cookies(_con_x(settings, tmp_path))

    assert check.status is CheckStatus.ERROR
    # Lo que importa de un fallo es la linea de como arreglarlo.
    assert "scrappy cookies" in check.fix


def test_unas_cookies_incompletas_se_detectan(settings: Settings, tmp_path: Path) -> None:
    """Sin `ct0` no hay csrf, y X responde 403 a todo."""
    from scrappy.diagnostics import check_x_cookies

    ruta = _fichero_de_cookies(tmp_path, "auth_token")
    check = check_x_cookies(_con_x(settings, tmp_path, x_cookies_file=ruta))

    assert check.status is CheckStatus.ERROR
    assert "ct0" in check.detail


def test_sin_navegador_se_avisa_pero_no_bloquea(settings: Settings, tmp_path: Path) -> None:
    """Funciona hoy; el problema llega el dia que caduque y no haya de donde."""
    from scrappy.diagnostics import check_x_cookies

    ruta = _fichero_de_cookies(tmp_path, "auth_token", "ct0")
    check = check_x_cookies(_con_x(settings, tmp_path, x_cookies_file=ruta))

    assert check.status is CheckStatus.WARNING
    assert "SCRAPPY_X_COOKIES_BROWSER" in check.fix


def test_todo_en_orden(settings: Settings, tmp_path: Path) -> None:
    from scrappy.diagnostics import check_x_cookies

    ruta = _fichero_de_cookies(tmp_path, "auth_token", "ct0")
    check = check_x_cookies(
        _con_x(settings, tmp_path, x_cookies_file=ruta, x_cookies_browser="firefox:burner")
    )

    assert check.status is CheckStatus.OK


# ---------------------------------------------------------------------------
# El ritmo
#
# `objetivos_por_ronda` y el intervalo del scheduler se multiplican, y se
# configuran en pantallas distintas. Esta comprobacion es el unico sitio donde
# se ven juntos.
# ---------------------------------------------------------------------------
def test_el_ritmo_se_calcula_de_las_dos_cosas() -> None:
    from scrappy.sources.x import ritmo

    # Una ronda cada 4 h: la ventana de 15 min mas cargada solo ve una ronda.
    assert ritmo(3, 240) == (18, 3)
    # Cada 5 minutos caben tres rondas en la ventana.
    assert ritmo(10, 5) == (2880, 30)
    # Valores absurdos no revientan: se tratan como 1 y 1 minuto, que es el
    # peor caso, no como una division por cero.
    assert ritmo(0, 0) == (1440, 15)


def _catalogo_con_x(tmp_path: Path, cuentas: int, por_ronda: int) -> Path:
    ruta = tmp_path / "sources.yaml"
    lista = "\n".join(f"      - cuenta{i}" for i in range(cuentas))
    ruta.write_text(
        "ranking:\n  weights:\n    engagement: 0.5\n    velocity: 0.5\n"
        "sources:\n  x:\n"
        f"    objetivos_por_ronda: {por_ronda}\n"
        f"    accounts:\n{lista}\n",
        encoding="utf-8",
    )
    return ruta


def test_un_ritmo_normal_no_molesta(settings: Settings, tmp_path: Path) -> None:
    from scrappy.diagnostics import check_x_ritmo

    catalogo = _catalogo_con_x(tmp_path, cuentas=5, por_ronda=3)
    check = check_x_ritmo(
        _con_x(settings, tmp_path, sources_config_path=catalogo, schedule_interval_minutes=240)
    )

    assert check.status is CheckStatus.OK
    assert "500" in check.detail


def test_un_ritmo_agresivo_se_avisa(settings: Settings, tmp_path: Path) -> None:
    """Subir uno de los dos numeros sin mirar el otro es el fallo facil."""
    from scrappy.diagnostics import check_x_ritmo

    catalogo = _catalogo_con_x(tmp_path, cuentas=200, por_ronda=200)
    check = check_x_ritmo(
        _con_x(settings, tmp_path, sources_config_path=catalogo, schedule_interval_minutes=5)
    )

    assert check.status is CheckStatus.ERROR
    assert "429" in check.fix


def test_no_se_cuentan_mas_perfiles_de_los_que_hay(settings: Settings, tmp_path: Path) -> None:
    """Pedir 10 por ronda con 2 cuentas son 2 peticiones, no 10."""
    from scrappy.diagnostics import check_x_ritmo

    catalogo = _catalogo_con_x(tmp_path, cuentas=2, por_ronda=10)
    check = check_x_ritmo(
        _con_x(settings, tmp_path, sources_config_path=catalogo, schedule_interval_minutes=240)
    )

    assert check.detail.startswith("12 perfiles al dia")
