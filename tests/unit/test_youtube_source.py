"""Pruebas del adapter de YouTube Shorts.

No tocan la red: se sustituye el motor de yt-dlp por un doble que devuelve
entradas con la forma que produce `extract_flat`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import ToSAcknowledgementRequiredError
from scrappy.core.models import MediaKind
from scrappy.sources.youtube import YouTubeSource


@pytest.fixture
def yt_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"youtube_enabled": True, "enable_tos_risky_sources": True})


@pytest.fixture
def yt_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "weight": 0.7,
            "budget": 40,
            "results_per_query": 20,
            "queries": ["shorts memes graciosos"],
            "max_duration": 180,
        }
    )


def _entry(**overrides: Any) -> dict[str, Any]:
    """Entrada tal y como la produce `extract_flat` de yt-dlp."""
    entry: dict[str, Any] = {
        "id": "dQw4w9WgXcQ",
        "title": "Un short gracioso",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "uploader": "CanalDePruebas",
        "uploader_url": "https://www.youtube.com/@CanalDePruebas",
        "duration": 45,
        "view_count": 250_000,
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg",
        "timestamp": 1_754_900_000,
    }
    entry.update(overrides)
    return entry


class FakeEngine:
    """Motor de yt-dlp de mentira: devuelve entradas fijas y apunta las URLs."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries
        self.urls: list[str] = []

    async def enumerate(self, url: str, *, limit: int, **_kwargs: Any) -> list[dict[str, Any]]:
        self.urls.append(url)
        return self._entries[:limit]


def _source(
    settings: Settings, config: SourceConfig, entries: list[dict[str, Any]]
) -> tuple[YouTubeSource, FakeEngine]:
    source = YouTubeSource(settings, config, httpx.AsyncClient())
    engine = FakeEngine(entries)
    source._engine = engine  # type: ignore[assignment]
    return source, engine


# ---------------------------------------------------------------------------
# Construccion de URLs
# ---------------------------------------------------------------------------
def test_usa_la_busqueda_interna_de_ytdlp(yt_settings: Settings, yt_config: SourceConfig) -> None:
    """`ytsearchN:` no depende de que la URL de resultados de YouTube siga igual."""
    source, _ = _source(yt_settings, yt_config, [])

    urls = source.collection_urls()

    assert urls == ["ytsearch20:shorts memes graciosos"]


def test_acepta_canales_ademas_de_busquedas(yt_settings: Settings) -> None:
    config = SourceConfig.model_validate(
        {"queries": ["memes"], "channels": ["@MrBeast", "OtroCanal"]}
    )
    source, _ = _source(yt_settings, config, [])

    urls = source.collection_urls()

    assert "https://www.youtube.com/@MrBeast/shorts" in urls
    assert "https://www.youtube.com/@OtroCanal/shorts" in urls


# ---------------------------------------------------------------------------
# Normalizacion y descartes
# ---------------------------------------------------------------------------
async def test_normaliza_un_short(yt_settings: Settings, yt_config: SourceConfig) -> None:
    source, _ = _source(yt_settings, yt_config, [_entry()])

    candidates = await source.discover(40)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "youtube"
    assert candidate.source_id == "dQw4w9WgXcQ"
    assert candidate.author == "CanalDePruebas"
    assert candidate.engagement == 250_000
    assert candidate.duration_seconds == 45
    assert candidate.kind is MediaKind.VIDEO
    # Siempre via yt-dlp: hay que unir video y audio.
    assert candidate.media_url is None


async def test_descarta_los_videos_largos(yt_settings: Settings, yt_config: SourceConfig) -> None:
    """Las busquedas de YouTube cuelan videos normales entre los Shorts."""
    source, _ = _source(
        yt_settings,
        yt_config,
        [_entry(id="corto", duration=45), _entry(id="largo", duration=1800)],
    )

    candidates = await source.discover(40)

    assert [c.source_id for c in candidates] == ["corto"]


async def test_descarta_directos_y_estrenos(yt_settings: Settings, yt_config: SourceConfig) -> None:
    """No son contenido corto y no se descargan de forma fiable en emision."""
    source, _ = _source(
        yt_settings,
        yt_config,
        [
            _entry(id="directo", is_live=True),
            _entry(id="estreno", live_status="is_upcoming"),
            _entry(id="normal"),
        ],
    )

    candidates = await source.discover(40)

    assert [c.source_id for c in candidates] == ["normal"]


async def test_respeta_el_minimo_de_visualizaciones(yt_settings: Settings) -> None:
    config = SourceConfig.model_validate({"queries": ["memes"], "min_view_count": 100_000})
    source, _ = _source(
        yt_settings,
        config,
        [_entry(id="flojo", view_count=5_000), _entry(id="fuerte", view_count=900_000)],
    )

    candidates = await source.discover(40)

    assert [c.source_id for c in candidates] == ["fuerte"]


async def test_sin_duracion_conocida_no_se_descarta(
    yt_settings: Settings, yt_config: SourceConfig
) -> None:
    """`extract_flat` a veces no informa de la duracion; no es motivo para tirarlo."""
    source, _ = _source(yt_settings, yt_config, [_entry(duration=None)])

    candidates = await source.discover(40)

    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# La puerta de ToS
# ---------------------------------------------------------------------------
async def test_sin_consentimiento_no_arranca(settings: Settings, yt_config: SourceConfig) -> None:
    """Descargar de YouTube tambien incumple sus terminos: va detras del flag."""
    sin_flag = settings.model_copy(
        update={"youtube_enabled": True, "enable_tos_risky_sources": False}
    )
    source, _ = _source(sin_flag, yt_config, [_entry()])

    with pytest.raises(ToSAcknowledgementRequiredError, match="LEGAL"):
        await source.discover(40)


async def test_status_refleja_el_bloqueo(settings: Settings, yt_config: SourceConfig) -> None:
    sin_flag = settings.model_copy(update={"youtube_enabled": True})
    source, _ = _source(sin_flag, yt_config, [])

    status = await source.status()

    assert not status.configured
    assert "ENABLE_TOS_RISKY_SOURCES" in status.detail


async def test_status_correcto_con_el_flag(yt_settings: Settings, yt_config: SourceConfig) -> None:
    source, _ = _source(yt_settings, yt_config, [])

    status = await source.status()

    assert status.configured
