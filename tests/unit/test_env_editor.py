"""Pruebas del editor de `.env`.

Dos cosas que verificar por encima del resto: que los comentarios sobreviven
—son la documentacion de cada variable— y que un fichero invalido no llega a
escribirse, porque un `.env` roto impide arrancar el proyecto entero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.core.errors import ConfigError
from scrappy.tui.env_editor import EnvEditor, is_secret

ENV_CON_COMENTARIOS = """\
# =============================================================================
# Scrappy -- configuracion de ejemplo
# =============================================================================

# -----------------------------------------------------------------------------
# Telegram  (OBLIGATORIO)
# -----------------------------------------------------------------------------
# Token que te da @BotFather al crear el bot con /newbot
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU

# OJO CON EL SIGNO: en un chat privado el id va POSITIVO.
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148

SCRAPPY_ITEMS_PER_RUN=5

# Reddit ya no necesita credenciales.
SCRAPPY_REDDIT_USER_AGENT=windows:scrappy:0.1.0 (by /u/pruebas)
"""


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(ENV_CON_COMENTARIOS, encoding="utf-8")
    return path


@pytest.fixture
def editor(env_path: Path) -> EnvEditor:
    editor = EnvEditor(env_path)
    editor.load()
    return editor


# ---------------------------------------------------------------------------
# LA prueba
# ---------------------------------------------------------------------------
def test_los_comentarios_sobreviven(editor: EnvEditor, env_path: Path) -> None:
    """Los comentarios documentan cada variable; perderlos seria un retroceso."""
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "8")
    editor.save()

    guardado = env_path.read_text(encoding="utf-8")

    assert "Token que te da @BotFather" in guardado
    assert "OJO CON EL SIGNO" in guardado
    assert "Reddit ya no necesita credenciales" in guardado
    assert "SCRAPPY_ITEMS_PER_RUN=8" in guardado


def test_solo_cambia_la_linea_pedida(editor: EnvEditor, env_path: Path) -> None:
    original = env_path.read_text(encoding="utf-8")
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "9")
    editor.save()

    guardado = env_path.read_text(encoding="utf-8")
    diferencias = [
        (a, b)
        for a, b in zip(original.splitlines(), guardado.splitlines(), strict=False)
        if a != b
    ]
    assert len(diferencias) == 1
    assert "ITEMS_PER_RUN" in diferencias[0][1]


def test_conserva_el_orden(editor: EnvEditor, env_path: Path) -> None:
    editor.set_value("SCRAPPY_TELEGRAM_TARGET_CHAT_ID", "999")
    editor.save()

    guardado = env_path.read_text(encoding="utf-8")
    assert guardado.index("BOT_TOKEN") < guardado.index("TARGET_CHAT_ID")
    assert guardado.index("TARGET_CHAT_ID") < guardado.index("ITEMS_PER_RUN")


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------
def test_lee_valores(editor: EnvEditor) -> None:
    assert editor.get_value("SCRAPPY_ITEMS_PER_RUN") == "5"
    assert editor.get_value("SCRAPPY_NO_EXISTE", "por defecto") == "por defecto"


def test_lista_las_claves_en_orden(editor: EnvEditor) -> None:
    claves = editor.keys()
    assert claves[0] == "SCRAPPY_TELEGRAM_BOT_TOKEN"
    assert "SCRAPPY_ITEMS_PER_RUN" in claves


def test_anade_variables_que_no_estaban(editor: EnvEditor, env_path: Path) -> None:
    """A diferencia del YAML, aqui si se permite crear: no hay estructura que romper."""
    editor.set_value("SCRAPPY_LOG_LEVEL", "DEBUG")
    editor.save()

    assert "SCRAPPY_LOG_LEVEL=DEBUG" in env_path.read_text(encoding="utf-8")


def test_valores_con_espacios_y_parentesis(editor: EnvEditor) -> None:
    """El User-Agent de Reddit lleva ambos y no debe romper el parseo."""
    assert "(by /u/pruebas)" in editor.get_value("SCRAPPY_REDDIT_USER_AGENT")


def test_marca_los_cambios_pendientes(editor: EnvEditor) -> None:
    assert not editor.dirty
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "7")
    assert editor.dirty
    editor.save()
    assert not editor.dirty


def test_asignar_el_mismo_valor_no_ensucia(editor: EnvEditor) -> None:
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "5")
    assert not editor.dirty


# ---------------------------------------------------------------------------
# Secretos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "clave",
    [
        "SCRAPPY_TELEGRAM_BOT_TOKEN",
        "SCRAPPY_REDDIT_CLIENT_SECRET",
        "SCRAPPY_GIPHY_API_KEY",
        "SCRAPPY_X_COOKIES_FILE",
    ],
)
def test_detecta_las_variables_secretas(clave: str) -> None:
    """La interfaz las enmascara; sin esto el token se veria en pantalla."""
    assert is_secret(clave)


@pytest.mark.parametrize(
    "clave",
    ["SCRAPPY_ITEMS_PER_RUN", "SCRAPPY_LOG_LEVEL", "SCRAPPY_TELEGRAM_TARGET_CHAT_ID"],
)
def test_no_marca_como_secreto_lo_que_no_lo_es(clave: str) -> None:
    assert not is_secret(clave)


def test_no_registra_los_valores_en_el_log(
    editor: EnvEditor, caplog: pytest.LogCaptureFixture
) -> None:
    """Un token en el log seria tan malo como en pantalla."""
    with caplog.at_level("DEBUG"):
        editor.set_value("SCRAPPY_TELEGRAM_BOT_TOKEN", "8912040901:SECRETO_QUE_NO_DEBE_SALIR")

    assert "SECRETO_QUE_NO_DEBE_SALIR" not in caplog.text


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def test_valida_antes_de_escribir(editor: EnvEditor, env_path: Path) -> None:
    """Un .env roto impide arrancar: mejor conservar el que habia."""
    original = env_path.read_text(encoding="utf-8")
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "no-soy-un-numero")

    with pytest.raises(ConfigError, match="no es valida"):
        editor.save()

    assert env_path.read_text(encoding="utf-8") == original


def test_save_devuelve_los_ajustes_validados(editor: EnvEditor) -> None:
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "12")
    settings = editor.save()
    assert settings.items_per_run == 12


def test_valida_el_fichero_y_no_el_entorno(editor: EnvEditor) -> None:
    """Si validase el entorno, un .env de otra carpeta podria colarse."""
    editor.set_value("SCRAPPY_ITEMS_PER_RUN", "3")
    assert editor.validate().items_per_run == 3


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
def test_fichero_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="scrappy init"):
        EnvEditor(tmp_path / "no-existe").load()


def test_usar_sin_cargar(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="load"):
        EnvEditor(tmp_path / ".env").get_value("X")


def test_lineas_raras_se_conservan_intactas(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# solo comentario\n\n=valor sin clave\nSCRAPPY_LOG_LEVEL=INFO\n", "utf-8")

    editor = EnvEditor(path)
    editor.load()
    editor.set_value("SCRAPPY_LOG_LEVEL", "DEBUG")
    editor.save()

    guardado = path.read_text(encoding="utf-8")
    assert "=valor sin clave" in guardado
    assert "# solo comentario" in guardado
