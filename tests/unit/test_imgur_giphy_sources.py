"""Pruebas de Imgur y Giphy.

Ninguna de las dos se pudo verificar contra su API real porque hacen falta
claves, asi que los fixtures reproducen el formato **documentado** de cada API.
Es una diferencia importante respecto a Reddit, Lemmy y Bluesky, cuyos fixtures
salieron de respuestas capturadas de verdad.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.giphy import GiphySource
from scrappy.sources.imgur import ImgurSource

IMGUR_VIRAL = "https://api.imgur.com/3/gallery/hot/viral/day/0"
GIPHY_TRENDING = "https://api.giphy.com/v1/gifs/trending"


# ===========================================================================
# Imgur
# ===========================================================================
@pytest.fixture
def imgur_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"imgur_enabled": True, "imgur_client_id": SecretStr("cliente-de-prueba")}
    )


@pytest.fixture
def imgur_config() -> SourceConfig:
    return SourceConfig.model_validate({"budget": 50, "window": "day", "delay_seconds": 0})


def _imgur_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "abc123",
        "title": "Un meme",
        "account_url": "pepita",
        "datetime": 1_754_900_000,
        "link": "https://i.imgur.com/abc123.jpg",
        "type": "image/jpeg",
        "animated": False,
        "is_album": False,
        "ups": 4200,
        "comment_count": 130,
        "views": 90_000,
        "nsfw": False,
    }
    item.update(overrides)
    return item


def _imgur_payload(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(items), "success": True, "status": 200})


@respx.mock
async def test_imgur_normaliza(imgur_settings: Settings, imgur_config: SourceConfig) -> None:
    respx.get(IMGUR_VIRAL).mock(return_value=_imgur_payload(_imgur_item()))

    async with httpx.AsyncClient() as client:
        candidates = await ImgurSource(imgur_settings, imgur_config, client).discover(50)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "imgur"
    assert candidate.author == "pepita"
    assert candidate.engagement == 4200
    assert candidate.comments == 130
    assert candidate.kind is MediaKind.PHOTO


@respx.mock
async def test_imgur_prefiere_el_mp4_para_los_animados(
    imgur_settings: Settings, imgur_config: SourceConfig
) -> None:
    """Un GIF pesa mucho mas que el mismo contenido en mp4."""
    respx.get(IMGUR_VIRAL).mock(
        return_value=_imgur_payload(
            _imgur_item(
                animated=True,
                type="image/gif",
                link="https://i.imgur.com/abc123.gif",
                mp4="https://i.imgur.com/abc123.mp4",
            )
        )
    )

    async with httpx.AsyncClient() as client:
        candidates = await ImgurSource(imgur_settings, imgur_config, client).discover(50)

    assert candidates[0].kind is MediaKind.ANIMATION
    assert candidates[0].media_url == "https://i.imgur.com/abc123.mp4"


@respx.mock
async def test_imgur_desenvuelve_los_albumes(
    imgur_settings: Settings, imgur_config: SourceConfig
) -> None:
    """Un album no tiene medio propio: hay que bajar a su primera imagen."""
    respx.get(IMGUR_VIRAL).mock(
        return_value=_imgur_payload(
            _imgur_item(
                is_album=True,
                link="https://imgur.com/a/abc123",
                type=None,
                images=[
                    {
                        "link": "https://i.imgur.com/primera.png",
                        "type": "image/png",
                        "animated": False,
                    }
                ],
            )
        )
    )

    async with httpx.AsyncClient() as client:
        candidates = await ImgurSource(imgur_settings, imgur_config, client).discover(50)

    assert candidates[0].media_url == "https://i.imgur.com/primera.png"
    assert candidates[0].kind is MediaKind.PHOTO


@respx.mock
async def test_imgur_descarta_los_albumes_vacios(
    imgur_settings: Settings, imgur_config: SourceConfig
) -> None:
    respx.get(IMGUR_VIRAL).mock(return_value=_imgur_payload(_imgur_item(is_album=True, images=[])))

    async with httpx.AsyncClient() as client:
        candidates = await ImgurSource(imgur_settings, imgur_config, client).discover(50)

    assert candidates == []


@respx.mock
async def test_imgur_403_explica_la_cuota(
    imgur_settings: Settings, imgur_config: SourceConfig
) -> None:
    respx.get(IMGUR_VIRAL).mock(return_value=httpx.Response(403))

    async with httpx.AsyncClient() as client:
        source = ImgurSource(imgur_settings, imgur_config, client)
        with pytest.raises(SourceError, match="cuota"):
            await source._fetch("/gallery/hot/viral/day/0")


async def test_imgur_status_sin_clave(settings: Settings, imgur_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await ImgurSource(settings, imgur_config, client).status()

    assert not status.configured
    assert "addclient" in status.detail


# ===========================================================================
# Giphy
# ===========================================================================
@pytest.fixture
def giphy_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"giphy_enabled": True, "giphy_api_key": SecretStr("clave-de-prueba")}
    )


@pytest.fixture
def giphy_config() -> SourceConfig:
    return SourceConfig.model_validate({"budget": 40, "delay_seconds": 0})


def _giphy_item(item_id: str = "gif123", **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "title": "reaccion graciosa",
        "username": "canaldeprueba",
        "url": f"https://giphy.com/gifs/{item_id}",
        "import_datetime": "2026-08-11 09:00:00",
        "rating": "pg",
        "images": {
            "original": {
                "url": f"https://media.giphy.com/media/{item_id}/giphy.gif",
                "mp4": f"https://media.giphy.com/media/{item_id}/giphy.mp4",
                "width": "480",
                "height": "270",
            },
            "fixed_width_small": {"url": f"https://media.giphy.com/media/{item_id}/100w.gif"},
        },
    }
    item.update(overrides)
    return item


def _giphy_payload(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(items), "pagination": {}, "meta": {}})


@respx.mock
async def test_giphy_normaliza(giphy_settings: Settings, giphy_config: SourceConfig) -> None:
    respx.get(GIPHY_TRENDING).mock(return_value=_giphy_payload(_giphy_item()))

    async with httpx.AsyncClient() as client:
        candidates = await GiphySource(giphy_settings, giphy_config, client).discover(40)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "giphy"
    assert candidate.author == "canaldeprueba"
    assert candidate.kind is MediaKind.ANIMATION
    # Se prefiere el mp4: pesa una fraccion de lo que pesa el GIF.
    assert candidate.media_url.endswith(".mp4")


@respx.mock
async def test_giphy_deriva_el_engagement_de_la_posicion(
    giphy_settings: Settings, giphy_config: SourceConfig
) -> None:
    """Giphy no expone ningun contador: el orden de `trending` es la senal."""
    respx.get(GIPHY_TRENDING).mock(
        return_value=_giphy_payload(*(_giphy_item(f"g{i}") for i in range(5)))
    )

    async with httpx.AsyncClient() as client:
        candidates = await GiphySource(giphy_settings, giphy_config, client).discover(40)

    engagements = [c.engagement for c in candidates]
    assert engagements == sorted(engagements, reverse=True)
    assert min(engagements) >= 1


@respx.mock
async def test_giphy_marca_nsfw_por_clasificacion(
    giphy_settings: Settings, giphy_config: SourceConfig
) -> None:
    respx.get(GIPHY_TRENDING).mock(return_value=_giphy_payload(_giphy_item(rating="r")))

    async with httpx.AsyncClient() as client:
        candidates = await GiphySource(giphy_settings, giphy_config, client).discover(40)

    assert candidates[0].nsfw is True


@respx.mock
async def test_giphy_sin_medio_se_descarta(
    giphy_settings: Settings, giphy_config: SourceConfig
) -> None:
    respx.get(GIPHY_TRENDING).mock(return_value=_giphy_payload(_giphy_item(images={})))

    async with httpx.AsyncClient() as client:
        candidates = await GiphySource(giphy_settings, giphy_config, client).discover(40)

    assert candidates == []


@respx.mock
async def test_giphy_clave_rechazada_menciona_el_limite(
    giphy_settings: Settings, giphy_config: SourceConfig
) -> None:
    respx.get(GIPHY_TRENDING).mock(return_value=httpx.Response(401))

    async with httpx.AsyncClient() as client:
        source = GiphySource(giphy_settings, giphy_config, client)
        with pytest.raises(SourceError, match="100 llamadas"):
            await source._fetch(None, 25, "pg-13")


@respx.mock
async def test_giphy_consulta_tendencias_y_busquedas(giphy_settings: Settings) -> None:
    trending = respx.get(GIPHY_TRENDING).mock(return_value=_giphy_payload(_giphy_item("t1")))
    search = respx.get("https://api.giphy.com/v1/gifs/search").mock(
        return_value=_giphy_payload(_giphy_item("s1"))
    )
    config = SourceConfig.model_validate({"queries": ["fail"], "delay_seconds": 0})

    async with httpx.AsyncClient() as client:
        candidates = await GiphySource(giphy_settings, config, client).discover(40)

    assert trending.called and search.called
    assert {c.source_id for c in candidates} == {"t1", "s1"}


async def test_giphy_status_sin_clave(settings: Settings, giphy_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await GiphySource(settings, giphy_config, client).status()

    assert not status.configured
    assert "developers.giphy.com" in status.detail
