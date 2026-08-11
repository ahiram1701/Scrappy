"""Pruebas de la carga de configuracion."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.config.loader import SourcesConfig, load_sources_config
from scrappy.config.settings import Settings, StateBackend
from scrappy.core.errors import ConfigError


def test_sin_fichero_se_usan_los_valores_por_defecto(tmp_path: Path) -> None:
    """El proyecto tiene que arrancar recien clonado, sin copiar el ejemplo."""
    config = load_sources_config(tmp_path / "no-existe.yaml")
    assert isinstance(config, SourcesConfig)
    assert config.ranking.weights.engagement == 0.50


def test_yaml_invalido_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "roto.yaml"
    ruta.write_text("ranking: [esto no cierra", encoding="utf-8")

    with pytest.raises(ConfigError, match="no es YAML valido"):
        load_sources_config(ruta)


def test_esquema_incorrecto_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "malo.yaml"
    ruta.write_text("ranking:\n  weights:\n    inventado: 1.0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="esquema"):
        load_sources_config(ruta)


def test_raiz_que_no_es_un_mapa(tmp_path: Path) -> None:
    ruta = tmp_path / "lista.yaml"
    ruta.write_text("- uno\n- dos\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mapa en la raiz"):
        load_sources_config(ruta)


def test_las_claves_extra_de_una_fuente_se_conservan(tmp_path: Path) -> None:
    """Cada adapter define sus propias claves; el loader no debe estorbar."""
    ruta = tmp_path / "sources.yaml"
    ruta.write_text(
        "sources:\n"
        "  reddit:\n"
        "    weight: 0.8\n"
        "    subreddits: [memes, funny]\n"
        "    min_score: 1500\n",
        encoding="utf-8",
    )

    reddit = load_sources_config(ruta).for_source("reddit")

    assert reddit.weight == 0.8
    assert reddit.get_list("subreddits") == ["memes", "funny"]
    assert reddit.get_int("min_score", 0) == 1500
    assert reddit.get_list("no_existe") == []


def test_una_cadena_suelta_vale_como_lista_de_uno(tmp_path: Path) -> None:
    ruta = tmp_path / "sources.yaml"
    ruta.write_text("sources:\n  reddit:\n    subreddits: memes\n", encoding="utf-8")
    assert load_sources_config(ruta).for_source("reddit").get_list("subreddits") == ["memes"]


def test_los_pesos_no_pueden_sumar_cero(tmp_path: Path) -> None:
    ruta = tmp_path / "sources.yaml"
    ruta.write_text(
        "ranking:\n  weights:\n    engagement: 0\n    velocity: 0\n    source: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_sources_config(ruta)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_admin_ids_se_parsean_de_la_cadena() -> None:
    settings = Settings(telegram_admin_ids=" 1, 2 ,3 ")
    assert settings.admin_ids == frozenset({1, 2, 3})


def test_admin_ids_vacio_no_autoriza_a_nadie() -> None:
    assert Settings(telegram_admin_ids="").admin_ids == frozenset()


def test_admin_ids_no_numerico_da_error() -> None:
    with pytest.raises(ConfigError, match="no numerico"):
        _ = Settings(telegram_admin_ids="1,pepe").admin_ids


def test_el_limite_de_subida_respeta_el_de_telegram() -> None:
    """Aunque se pida 500 MB, Telegram no acepta mas de 50 desde un bot."""
    settings = Settings(max_download_mb=500)
    assert settings.upload_limit_bytes == 50 * 1024 * 1024


def test_el_limite_de_subida_respeta_el_nuestro_si_es_menor() -> None:
    settings = Settings(max_download_mb=10)
    assert settings.upload_limit_bytes == 10 * 1024 * 1024


def test_rango_de_duracion_incoherente() -> None:
    with pytest.raises(ValueError, match="min_duration_seconds"):
        Settings(min_duration_seconds=100, max_duration_seconds=10)


def test_validate_for_publishing_exige_token_y_chat() -> None:
    with pytest.raises(ConfigError, match="incompleta"):
        Settings(telegram_bot_token="", telegram_target_chat_id="").validate_for_publishing()


def test_fuentes_habilitadas() -> None:
    settings = Settings(
        reddit_enabled=True, lemmy_enabled=False, x_enabled=True, tiktok_enabled=False
    )
    assert set(settings.enabled_source_names()) == {"reddit", "x"}


def test_las_fuentes_sin_credenciales_vienen_activadas() -> None:
    """Reddit y Lemmy no piden claves, asi que el bot publica nada mas instalarlo."""
    activas = set(Settings().enabled_source_names())
    assert {"reddit", "lemmy"} <= activas
    # Las de riesgo de ToS, no.
    assert not {"tiktok", "instagram"} & activas


def test_backend_de_estado_por_defecto_es_sqlite() -> None:
    assert Settings().state_backend is StateBackend.SQLITE
