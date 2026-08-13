"""El scheduler visto desde la interfaz.

El fallo que motiva este fichero: la pantalla de Configuracion mostraba
«Scheduler activo» encendido y el Panel decia «parado», a la vez. Las dos
cosas eran ciertas y se referian a cosas distintas —una al fichero, otra a si
alguien lo habia arrancado— pero leidas juntas son una contradiccion.

`scrappy run` arranca el scheduler desde siempre; la TUI no lo hacia.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Static

from scrappy.tui.main import ScrappyTUI
from tests.unit.test_tui import _texto

BASE = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_STATE_BACKEND=memory
SCRAPPY_TIMEZONE=America/Mexico_City
SCRAPPY_SCHEDULE_INTERVAL_MINUTES=180
SCRAPPY_ITEMS_PER_RUN=5
"""


def _env(tmp_path: Path, extra: str = "") -> Path:
    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"{BASE}{extra}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n",
        encoding="utf-8",
    )
    return env_path


def _estado(pilot: object) -> str:
    return _texto(pilot.app.screen.query_one("#estado-scheduler", Static))  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# El fallo
# ---------------------------------------------------------------------------
async def test_activo_en_la_configuracion_significa_en_marcha(tmp_path: Path) -> None:
    """Es el fallo, en una linea: la configuracion decia si y el panel no."""
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert tui.scheduler.running, "con SCHEDULE_ENABLED=true tiene que estar en marcha"

        texto = _estado(pilot)
        assert "en marcha" in texto
        assert "parado" not in texto
        # Y con la informacion que hace falta para saber que va a pasar.
        assert "5 items cada 180 min" in texto
        assert "proxima ronda" in texto


async def test_la_tui_hace_lo_mismo_que_scrappy_run(tmp_path: Path) -> None:
    """Dos interfaces del mismo proyecto no pueden interpretar distinto.

    `cli.run()` llama a `scheduler.start()` sin condiciones y deja que sea el
    propio scheduler quien mire `schedule_enabled`. La TUI hace ahora eso.
    """
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert tui.scheduler.enabled
        assert tui.scheduler.running


# ---------------------------------------------------------------------------
# Los tres motivos por los que puede no haber rondas
# ---------------------------------------------------------------------------
async def test_desactivado_lo_dice_y_no_ofrece_arrancar(tmp_path: Path) -> None:
    """Antes ponia «parado» y el boton «Arrancar» no hacia absolutamente nada."""
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=false\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert not tui.scheduler.running

        texto = _estado(pilot)
        assert "desactivado en la configuracion" in texto
        # Y dice donde cambiarlo.
        assert "Programacion" in texto
        assert pilot.app.screen.query_one("#arrancar", Button).disabled


async def test_sin_telegram_lo_dice_y_no_arranca(tmp_path: Path) -> None:
    """Arrancarlo sin publisher seria un fallo cada tres horas y nada mas."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SCRAPPY_STATE_BACKEND=memory\nSCRAPPY_SCHEDULE_ENABLED=true\n", encoding="utf-8"
    )

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert not tui.can_publish
        assert tui.scheduler is not None
        assert not tui.scheduler.running

        assert "falta configurar Telegram" in _estado(pilot)
        assert pilot.app.screen.query_one("#arrancar", Button).disabled


# ---------------------------------------------------------------------------
# Los botones
# ---------------------------------------------------------------------------
async def test_los_botones_reflejan_lo_que_se_puede_hacer(tmp_path: Path) -> None:
    """Un boton habilitado que no hace nada es peor que uno deshabilitado."""
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        pantalla = pilot.app.screen

        # En marcha: no se puede arrancar otra vez, si pausar.
        assert pantalla.query_one("#arrancar", Button).disabled
        assert not pantalla.query_one("#pausar", Button).disabled
        assert pantalla.query_one("#reanudar", Button).disabled

        pantalla.query_one("#pausar", Button).press()
        await pilot.pause()

        assert pantalla.query_one("#pausar", Button).disabled
        assert not pantalla.query_one("#reanudar", Button).disabled
        assert "en pausa" in _estado(pilot)

        pantalla.query_one("#reanudar", Button).press()
        await pilot.pause()

        assert "en marcha" in _estado(pilot)


# ---------------------------------------------------------------------------
# Recarga
# ---------------------------------------------------------------------------
async def test_activarlo_y_recargar_lo_pone_en_marcha(tmp_path: Path) -> None:
    """El camino natural: lo activas en Configuracion, guardas, y funciona."""
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=false\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert not tui.scheduler.running

        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace(
                "SCRAPPY_SCHEDULE_ENABLED=false", "SCRAPPY_SCHEDULE_ENABLED=true"
            ),
            encoding="utf-8",
        )
        assert await tui.recargar() is True
        await pilot.pause()

        assert tui.scheduler is not None
        assert tui.scheduler.running
        assert "en marcha" in _estado(pilot)


async def test_recargar_no_deshace_una_pausa(tmp_path: Path) -> None:
    """`paused` vive en `ScrappyApp`, que la recarga tira entera.

    Sin conservarlo, guardar cualquier ajuste reanudaba un scheduler que se
    habia pausado a proposito, sin decir nada.
    """
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scrappy is not None
        tui.scrappy.paused = True

        await tui.recargar()
        await pilot.pause()

        assert tui.scrappy is not None
        assert tui.scrappy.paused, "la pausa se perdio al recargar"
        assert "en pausa" in _estado(pilot)


async def test_desactivarlo_y_recargar_lo_para(tmp_path: Path) -> None:
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert tui.scheduler.running

        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace(
                "SCRAPPY_SCHEDULE_ENABLED=true", "SCRAPPY_SCHEDULE_ENABLED=false"
            ),
            encoding="utf-8",
        )
        await tui.recargar()
        await pilot.pause()

        assert tui.scheduler is not None
        assert not tui.scheduler.running
        assert "desactivado en la configuracion" in _estado(pilot)


# ---------------------------------------------------------------------------
# Que se VEA, no solo que este
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filas", [24, 30, 40], ids=lambda n: f"{n}-filas")
async def test_la_hora_de_la_proxima_ronda_cabe_en_pantalla(tmp_path: Path, filas: int) -> None:
    """`render()` devuelve el texto entero aunque en pantalla este recortado.

    Las secciones del panel heredaban el `height: 1fr` de `Vertical`, asi que
    se repartian el alto disponible y cada una cortaba lo que no cabia. Al
    pasar el estado del scheduler a dos lineas, la segunda -justo la de la
    hora- desaparecio sin ninguna senal de que faltara algo.

    Por eso esto mira el render de la pantalla y no el contenido del widget:
    es la unica forma de detectar un recorte.
    """
    env_path = _env(tmp_path, "SCRAPPY_SCHEDULE_ENABLED=true\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test(
        size=(100, filas)
    ) as pilot:
        await pilot.pause()
        visible = pilot.app.export_screenshot().replace("&#160;", " ")

    assert "en marcha:" in visible
    assert "proxima ronda" in visible, f"recortado en una terminal de {filas} filas"


# ---------------------------------------------------------------------------
# Coherencia entre las dos pantallas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("activo", [True, False], ids=["activo", "desactivado"])
async def test_el_panel_y_la_configuracion_dicen_lo_mismo(tmp_path: Path, activo: bool) -> None:
    """La comprobacion directa del fallo: leer las dos pantallas y compararlas."""
    from textual.widgets import Switch

    env_path = _env(tmp_path, f"SCRAPPY_SCHEDULE_ENABLED={'true' if activo else 'false'}\n")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        panel_en_marcha = tui.scheduler is not None and tui.scheduler.running

        await pilot.press("s")
        await pilot.pause()
        interruptor = pilot.app.screen.query_one("#env-SCRAPPY_SCHEDULE_ENABLED", Switch).value

        assert panel_en_marcha == interruptor == activo
