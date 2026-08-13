"""Pruebas de la recarga en caliente.

Los ajustes se leen una sola vez, al construir `ScrappyApp`: se reparten por
los adapters, el cliente y el backend de estado, y ninguno vuelve a mirarlos.
Por eso guardar el `.env` no bastaba y habia que reiniciar el proceso.

Lo que se comprueba aqui es que recargar hace lo mismo que un reinicio -montar
todo de nuevo con los ajustes releidos- sin cerrar la ventana.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.tui.main import ScrappyTUI

ENV_BASE = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_STATE_BACKEND=memory
SCRAPPY_ITEMS_PER_RUN=5
SCRAPPY_TIMEZONE=America/Mexico_City
"""


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    ruta = tmp_path / ".env"
    ruta.write_text(ENV_BASE, encoding="utf-8")

    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")
    ruta.write_text(
        f"{ENV_BASE}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n",
        encoding="utf-8",
    )
    return ruta


async def test_recargar_aplica_lo_que_hay_en_el_fichero(env_path: Path) -> None:
    """El caso de uso: se edita fuera, se recarga, y vale sin reiniciar."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scrappy is not None
        assert tui.scrappy.settings.items_per_run == 5

        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace(
                "SCRAPPY_ITEMS_PER_RUN=5", "SCRAPPY_ITEMS_PER_RUN=9"
            ),
            encoding="utf-8",
        )

        assert await tui.recargar() is True
        assert tui.scrappy is not None
        assert tui.scrappy.settings.items_per_run == 9


async def test_recargar_no_cierra_la_ventana(env_path: Path) -> None:
    """La diferencia con reiniciar: la sesion sobrevive."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        pantalla = type(tui.screen)

        await tui.recargar()
        await pilot.pause()

        assert tui.is_running
        assert type(tui.screen) is pantalla


async def test_la_aplicacion_vieja_se_cierra_antes_de_montar_la_nueva(env_path: Path) -> None:
    """Dos `ScrappyApp` vivos serian dos backends sobre la misma base."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        anterior = tui.scrappy
        assert anterior is not None

        await tui.recargar()

        assert tui.scrappy is not anterior
        # El cliente HTTP de la anterior tiene que estar cerrado.
        assert anterior.client.is_closed


async def test_la_zona_horaria_nueva_llega_al_scheduler(env_path: Path) -> None:
    from zoneinfo import ZoneInfo

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.scheduler is not None
        assert tui.scheduler._scheduler.timezone == ZoneInfo("America/Mexico_City")

        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace("America/Mexico_City", "Europe/Madrid"),
            encoding="utf-8",
        )
        await tui.recargar()

        assert tui.scheduler is not None
        assert tui.scheduler._scheduler.timezone == ZoneInfo("Europe/Madrid")


async def test_un_env_roto_no_tumba_la_sesion(env_path: Path) -> None:
    """Recargar sobre un fichero invalido debe avisar, no dejar la TUI muerta."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]

        env_path.write_text(f"{ENV_BASE}SCRAPPY_ITEMS_PER_RUN=no-soy-un-numero\n", encoding="utf-8")

        assert await tui.recargar() is False
        assert tui.is_running


async def test_guardar_desde_la_pantalla_ya_aplica(env_path: Path) -> None:
    """El motivo de todo esto: guardar decia «reinicia Scrappy»."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Input

        pilot.app.screen.query_one("#env-SCRAPPY_ITEMS_PER_RUN", Input).value = "8"
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert tui.scrappy is not None
        assert tui.scrappy.settings.items_per_run == 8
