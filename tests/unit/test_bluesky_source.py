"""Pruebas del adapter de Bluesky.

El fixture reproduce la forma real de `app.bsky.feed.searchPosts` en
`api.bsky.app`, con sus tres tipos de embed: imagenes, video y el
`recordWithMedia` que anida el medio un nivel mas abajo.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import SourceError
from scrappy.core.models import MediaKind, utcnow
from scrappy.sources.bluesky import BlueskySource

SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

_IMAGES_EMBED = {
    "$type": "app.bsky.embed.images#view",
    "images": [
        {
            "thumb": "https://cdn.bsky.app/img/feed_thumbnail/plain/did/x@jpeg",
            "fullsize": "https://cdn.bsky.app/img/feed_fullsize/plain/did/x@jpeg",
        }
    ],
}

_VIDEO_EMBED = {
    "$type": "app.bsky.embed.video#view",
    "playlist": "https://video.bsky.app/watch/did/cid/playlist.m3u8",
    "thumbnail": "https://video.bsky.app/watch/did/cid/thumbnail.jpg",
}


@pytest.fixture
def bluesky_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {"weight": 0.75, "budget": 60, "queries": ["meme"], "delay_seconds": 0}
    )


def _post(**overrides: Any) -> dict[str, Any]:
    post: dict[str, Any] = {
        "uri": "at://did:plc:abc123/app.bsky.feed.post/3lc2fcbcouk25",
        "cid": "bafy...",
        "author": {
            "did": "did:plc:abc123",
            "handle": "pepita.bsky.social",
            "displayName": "Pepita",
        },
        "record": {
            "text": "esto es un meme",
            "createdAt": "2026-08-11T09:00:00.000Z",
            "langs": ["es"],
        },
        "embed": dict(_IMAGES_EMBED),
        "likeCount": 5000,
        "repostCount": 900,
        "replyCount": 120,
        "quoteCount": 30,
        "indexedAt": "2026-08-11T09:01:00.000Z",
        "labels": [],
    }
    post.update(overrides)
    return post


def _payload(*posts: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"posts": list(posts), "cursor": "x", "hitsTotal": 1})


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------
@respx.mock
async def test_normaliza_un_post(settings: Settings, bluesky_config: SourceConfig) -> None:
    respx.get(SEARCH_URL).mock(return_value=_payload(_post()))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "bluesky"
    assert candidate.author == "Pepita"
    assert candidate.language == "es"
    # Likes + reposts: los dos son senales de difusion.
    assert candidate.engagement == 5900
    assert candidate.comments == 120
    assert candidate.kind is MediaKind.PHOTO


@respx.mock
async def test_el_permalink_se_compone_del_handle_y_el_rkey(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    """Un `at://` no es navegable: hay que reconstruir la URL publica."""
    respx.get(SEARCH_URL).mock(return_value=_payload(_post()))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert candidates[0].permalink == (
        "https://bsky.app/profile/pepita.bsky.social/post/3lc2fcbcouk25"
    )
    assert candidates[0].source_id == "3lc2fcbcouk25"
    assert not candidates[0].permalink.startswith("at://")


@respx.mock
async def test_las_imagenes_traen_url_directa(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    respx.get(SEARCH_URL).mock(return_value=_payload(_post()))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert candidates[0].media_url is not None
    assert "fullsize" in candidates[0].media_url
    assert candidates[0].thumbnail_url is not None


@respx.mock
async def test_el_video_se_delega_en_ytdlp(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    """El video se sirve como HLS, que hay que remuxear: no vale la URL directa."""
    respx.get(SEARCH_URL).mock(return_value=_payload(_post(embed=dict(_VIDEO_EMBED))))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert candidates[0].kind is MediaKind.VIDEO
    assert candidates[0].media_url is None
    assert candidates[0].thumbnail_url is not None


@respx.mock
async def test_desenvuelve_record_with_media(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    """Una cita con imagen anida el medio real bajo `media`."""
    anidado = {
        "$type": "app.bsky.embed.recordWithMedia#view",
        "record": {"record": {"uri": "at://otro"}},
        "media": dict(_IMAGES_EMBED),
    }
    respx.get(SEARCH_URL).mock(return_value=_payload(_post(embed=anidado)))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert len(candidates) == 1
    assert candidates[0].kind is MediaKind.PHOTO


@respx.mock
@pytest.mark.parametrize(
    "embed",
    [
        {},  # post de solo texto
        {"$type": "app.bsky.embed.external#view", "external": {"uri": "https://x.test"}},
        {"$type": "app.bsky.embed.images#view", "images": []},
        {"$type": "app.bsky.embed.record#view", "record": {}},
    ],
)
async def test_descarta_lo_que_no_trae_medio(
    settings: Settings, bluesky_config: SourceConfig, embed: dict[str, Any]
) -> None:
    respx.get(SEARCH_URL).mock(return_value=_payload(_post(embed=embed)))

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert candidates == []


@respx.mock
async def test_las_etiquetas_de_moderacion_marcan_nsfw(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=_payload(_post(labels=[{"val": "porn", "src": "did:plc:x"}]))
    )

    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, bluesky_config, client).discover(60)

    assert candidates[0].nsfw is True


# ---------------------------------------------------------------------------
# La ventana temporal
# ---------------------------------------------------------------------------
@respx.mock
async def test_acota_la_busqueda_a_la_ventana_configurada(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    """Sin `since`, `sort=top` devuelve lo mas votado de SIEMPRE.

    En las pruebas contra la API real salian posts de 2024 con 58.000 likes,
    que no sirven de senal temprana de nada.
    """
    route = respx.get(SEARCH_URL).mock(return_value=_payload(_post()))

    async with httpx.AsyncClient() as client:
        await BlueskySource(settings, bluesky_config, client).discover(60)

    params = route.calls[0].request.url.params
    assert params["sort"] == "top"
    assert "since" in params

    since = params["since"]
    esperado = utcnow() - timedelta(hours=24)
    # Basta con comprobar el dia: el segundo exacto depende del reloj.
    assert since.startswith(esperado.strftime("%Y-%m-%d"))


@respx.mock
async def test_la_ventana_es_configurable(settings: Settings) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=_payload(_post()))
    config = SourceConfig.model_validate(
        {"queries": ["meme"], "window_hours": 6, "delay_seconds": 0}
    )

    async with httpx.AsyncClient() as client:
        await BlueskySource(settings, config, client).discover(60)

    since = route.calls[0].request.url.params["since"]
    esperado = utcnow() - timedelta(hours=6)
    assert since.startswith(esperado.strftime("%Y-%m-%dT%H"))


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
@respx.mock
async def test_el_403_menciona_el_host_correcto(
    settings: Settings, bluesky_config: SourceConfig
) -> None:
    """`public.api.bsky.app` rechaza las busquedas desde mediados de 2026."""
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))

    async with httpx.AsyncClient() as client:
        source = BlueskySource(settings, bluesky_config, client)
        with pytest.raises(SourceError, match="public.api.bsky.app"):
            await source._search("meme", 25, utcnow())


@respx.mock
async def test_una_consulta_rota_no_tumba_la_fuente(settings: Settings) -> None:
    respx.get(SEARCH_URL, params={"q": "rota"}).mock(return_value=httpx.Response(500))
    respx.get(SEARCH_URL, params={"q": "buena"}).mock(return_value=_payload(_post()))

    config = SourceConfig.model_validate({"queries": ["rota", "buena"], "delay_seconds": 0})
    async with httpx.AsyncClient() as client:
        candidates = await BlueskySource(settings, config, client).discover(60)

    assert len(candidates) == 1


@respx.mock
async def test_respuesta_sin_posts(settings: Settings, bluesky_config: SourceConfig) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))

    async with httpx.AsyncClient() as client:
        assert await BlueskySource(settings, bluesky_config, client).discover(60) == []


async def test_status_sin_consultas(settings: Settings) -> None:
    async with httpx.AsyncClient() as client:
        status = await BlueskySource(settings, SourceConfig(), client).status()

    assert not status.configured
    assert "queries" in status.detail


async def test_status_correcto(settings: Settings, bluesky_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await BlueskySource(settings, bluesky_config, client).status()

    assert status.configured
    assert "sin credenciales" in status.detail
