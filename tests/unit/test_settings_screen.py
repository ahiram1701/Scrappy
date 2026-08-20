"""Pruebas de la pantalla de configuracion.

La primera reproduce un fallo real: la pantalla leia el `.env` en crudo, asi que
una clave ausente -que el bot resolvia con su valor por defecto- se pintaba como
«apagado», y al guardar se escribia ese `false`. Asi se apagaron dos fuentes que
llevaban semanas funcionando.

La segunda evita que los campos declarados se desincronicen del codigo real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.config.loader import load_sources_config
from scrappy.config.settings import Settings, StateBackend
from scrappy.tui.fields import (
    CONTENT_FIELDS,
    DELIVERY_FIELDS,
    FILTER_FIELDS,
    RANKING_FIELDS,
    SCHEDULE_FIELDS,
    SOURCE_ENABLED_KEY,
    SOURCE_EXTRA_ENV,
    STORAGE_FIELDS,
    TELEGRAM_FIELDS,
    EnvField,
    YamlField,
    fields_for_source,
)
from scrappy.tui.main import ScrappyTUI

#: Un `.env` como el que tenia el usuario: creado antes de que existieran
#: Lemmy y Bluesky, asi que sin esas claves.
ENV_ANTIGUO = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_REDDIT_ENABLED=true
SCRAPPY_ITEMS_PER_RUN=5
"""


@pytest.fixture
def entorno(tmp_path: Path) -> tuple[Settings, Path]:
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_ANTIGUO, encoding="utf-8")

    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_bot_token="8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU",  # type: ignore[arg-type]
        telegram_target_chat_id="1412545148",
        state_backend=StateBackend.MEMORY,
        workspace_root=tmp_path / "ws",
        sources_config_path=yaml_path,
    )
    return settings, env_path


# ---------------------------------------------------------------------------
# LA prueba: el bug que apago dos fuentes
# ---------------------------------------------------------------------------
async def test_una_clave_ausente_muestra_el_valor_efectivo(
    entorno: tuple[Settings, Path],
) -> None:
    """Lemmy no esta en el `.env`, pero el bot la da por activada.

    Pintarla como «apagada» seria mentir sobre lo que hace de verdad, y fue el
    primer paso del fallo.
    """
    settings, env_path = entorno
    assert "LEMMY" not in env_path.read_text(encoding="utf-8")
    assert settings.lemmy_enabled is True  # el bot si la usa

    async with ScrappyTUI(settings, env_path=env_path, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Switch

        interruptor = pilot.app.screen.query_one("#env-SCRAPPY_LEMMY_ENABLED", Switch)
        assert interruptor.value is True


async def test_guardar_no_apaga_lo_que_no_se_toco(entorno: tuple[Settings, Path]) -> None:
    """El fallo completo: guardar escribia `false` en fuentes que funcionaban."""
    settings, env_path = entorno

    async with ScrappyTUI(settings, env_path=env_path, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    guardado = env_path.read_text(encoding="utf-8")
    # Ni se apaga ni se materializa a la fuerza: no se toco, no se escribe.
    assert "SCRAPPY_LEMMY_ENABLED=false" not in guardado
    assert "SCRAPPY_BLUESKY_ENABLED=false" not in guardado


async def test_solo_se_escribe_lo_que_cambia(entorno: tuple[Settings, Path]) -> None:
    """Reescribir las ~40 claves en cada guardado es lo que causo el estropicio."""
    settings, env_path = entorno
    original = env_path.read_text(encoding="utf-8")

    async with ScrappyTUI(settings, env_path=env_path, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Input

        pilot.app.screen.query_one("#env-SCRAPPY_ITEMS_PER_RUN", Input).value = "7"
        await pilot.press("ctrl+s")
        await pilot.pause()

    guardado = env_path.read_text(encoding="utf-8")
    assert "SCRAPPY_ITEMS_PER_RUN=7" in guardado

    # Una sola linea distinta en todo el fichero.
    diferencias = [
        (a, b) for a, b in zip(original.splitlines(), guardado.splitlines(), strict=False) if a != b
    ]
    assert len(diferencias) == 1


async def test_apagar_una_fuente_a_proposito_si_funciona(
    entorno: tuple[Settings, Path],
) -> None:
    """El arreglo no puede impedir apagar algo cuando se quiere de verdad."""
    settings, env_path = entorno

    async with ScrappyTUI(settings, env_path=env_path, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Switch

        pilot.app.screen.query_one("#env-SCRAPPY_LEMMY_ENABLED", Switch).value = False
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert "SCRAPPY_LEMMY_ENABLED=false" in env_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Cobertura: los campos declarados tienen que existir de verdad
# ---------------------------------------------------------------------------
def _todos_los_env() -> list[EnvField]:
    campos = [*TELEGRAM_FIELDS, *CONTENT_FIELDS, *SCHEDULE_FIELDS, *STORAGE_FIELDS]
    campos += [EnvField(clave, clave) for clave in SOURCE_ENABLED_KEY.values()]
    # Los ajustes del `.env` propios de una fuente -el backend de X y su fichero
    # de cookies- se pintan en su pestana y merecen la misma comprobacion.
    for extra in SOURCE_EXTRA_ENV.values():
        campos.extend(extra)
    return campos


def _todos_los_yaml() -> list[YamlField]:
    campos = [*RANKING_FIELDS, *FILTER_FIELDS, *DELIVERY_FIELDS]
    for fuente in SOURCE_ENABLED_KEY:
        campos.extend(fields_for_source(fuente))
    return campos


@pytest.mark.parametrize("campo", _todos_los_env(), ids=lambda c: c.key)
def test_cada_campo_del_env_existe_en_settings(campo: EnvField) -> None:
    """Una errata en el nombre daria un campo que no configura nada."""
    atributo = campo.key.removeprefix("SCRAPPY_").lower()
    assert atributo in Settings.model_fields, f"{campo.key} no existe en Settings"


@pytest.mark.parametrize("campo", _todos_los_yaml(), ids=lambda c: ".".join(c.path))
def test_cada_campo_del_yaml_resuelve_en_el_ejemplo(campo: YamlField) -> None:
    """Si la ruta no existe en el YAML de ejemplo, el editor no podra escribirla.

    El editor de YAML no crea claves a proposito, asi que un campo declarado
    sobre una ruta inexistente seria un widget que no hace nada.
    """
    import yaml as pyyaml

    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    nodo = pyyaml.safe_load(ejemplo.read_text(encoding="utf-8"))
    for clave in campo.path:
        assert isinstance(nodo, dict) and clave in nodo, (
            f"{'.'.join(campo.path)} no existe en sources.example.yaml"
        )
        nodo = nodo[clave]


def test_el_yaml_de_ejemplo_sigue_siendo_valido() -> None:
    """Los tests de arriba lo dan por bueno; conviene comprobarlo."""
    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")
    assert load_sources_config(ejemplo) is not None
