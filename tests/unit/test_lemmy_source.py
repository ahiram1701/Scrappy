"""Pruebas del adapter de Lemmy.

El fixture reproduce la forma real de `/api/v3/post/list` en lemmy.world,
incluidas las banderas de post fijado y de cuenta bot, que son las que permiten
descartar con precision lo que en Reddit hay que adivinar por heuristica.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.lemmy import LemmySource

LIST_URL = "https://lemmy.world/api/v3/post/list"


@pytest.fixture
def lemmy_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {"weight": 0.8, "budget": 50, "sort": "TopDay", "delay_seconds": 0}
    )


def _view(**overrides: Any) -> dict[str, Any]:
    """Un `post_view` con la forma exacta que devuelve la API."""
    post: dict[str, Any] = {
        "id": 50505833,
        "name": "Me_irl",
        "url": "https://discuss.online/pictrs/image/ff346f79.jpeg",
        "url_content_type": "image/jpeg",
        "published": "2026-08-10T16:18:09.987523Z",
        "nsfw": False,
        "removed": False,
        "deleted": False,
        "thumbnail_url": "https://lemmy.world/pictrs/image/cbbb6aa5.jpeg",
        "ap_id": "https://discuss.online/post/43862140",
        "featured_community": False,
        "featured_local": False,
    }
    post.update(overrides.pop("post", {}))

    view: dict[str, Any] = {
        "post": post,
        "counts": {"post_id": post["id"], "comments": 90, "score": 797, "upvotes": 801},
        "creator": {
            "name": "alguien",
            "actor_id": "https://lemmy.world/u/alguien",
            "bot_account": False,
        },
        "community": {"name": "memes", "nsfw": False},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(view.get(key), dict):
            view[key] = {**view[key], **value}
        else:
            view[key] = value
    return view


def _payload(*views: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"posts": list(views), "next_page": None})


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------
@respx.mock
async def test_normaliza_un_post(settings: Settings, lemmy_config: SourceConfig) -> None:
    respx.get(LIST_URL).mock(return_value=_payload(_view()))

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "lemmy"
    assert candidate.source_id == "50505833"
    assert candidate.title == "Me_irl"
    assert candidate.author == "alguien"
    # A diferencia de Reddit, aqui hay engagement real.
    assert candidate.engagement == 797
    assert candidate.comments == 90
    assert candidate.kind is MediaKind.PHOTO
    assert candidate.media_url == "https://discuss.online/pictrs/image/ff346f79.jpeg"
    assert candidate.created_at.tzinfo is not None


@respx.mock
async def test_la_atribucion_apunta_a_la_instancia_de_origen(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    """En una red federada el post vive en su instancia, no en la que lo sirve.

    `ap_id` es la URL canonica, y es a donde debe ir el credito del autor.
    """
    respx.get(LIST_URL).mock(return_value=_payload(_view()))

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert candidates[0].permalink == "https://discuss.online/post/43862140"
    assert "lemmy.world" not in candidates[0].permalink


@respx.mock
@pytest.mark.parametrize(
    ("content_type", "url", "kind", "directo"),
    [
        ("image/jpeg", "https://x.test/a.jpeg", MediaKind.PHOTO, True),
        ("image/png", "https://x.test/a.png", MediaKind.PHOTO, True),
        ("image/gif", "https://x.test/a.gif", MediaKind.ANIMATION, True),
        ("video/mp4", "https://x.test/a.mp4", MediaKind.VIDEO, True),
        # Sin tipo MIME se recurre al dominio: yt-dlp lo resuelve.
        (None, "https://www.youtube.com/watch?v=abc", MediaKind.VIDEO, False),
        (None, "https://x.test/foto.png", MediaKind.PHOTO, True),
    ],
)
async def test_clasifica_por_tipo_mime_y_si_no_por_url(
    settings: Settings,
    lemmy_config: SourceConfig,
    content_type: str | None,
    url: str,
    kind: MediaKind,
    directo: bool,
) -> None:
    respx.get(LIST_URL).mock(
        return_value=_payload(_view(post={"url": url, "url_content_type": content_type}))
    )

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert candidates[0].kind is kind
    assert (candidates[0].media_url is not None) is directo


# ---------------------------------------------------------------------------
# Lo que se descarta
# ---------------------------------------------------------------------------
@respx.mock
@pytest.mark.parametrize(
    "descarte",
    [
        {"post": {"featured_community": True}},
        {"post": {"featured_local": True}},
        {"post": {"removed": True}},
        {"post": {"deleted": True}},
        {"post": {"url": None}},  # post de solo texto
        {"creator": {"bot_account": True}},
    ],
)
async def test_descarta_lo_que_no_procede(
    settings: Settings, lemmy_config: SourceConfig, descarte: dict[str, Any]
) -> None:
    respx.get(LIST_URL).mock(return_value=_payload(_view(**descarte)))

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert candidates == []


@respx.mock
async def test_descarta_los_enlaces_sin_medio(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    respx.get(LIST_URL).mock(
        return_value=_payload(
            _view(post={"url": "https://noticias.test/articulo", "url_content_type": None}),
            _view(post={"id": 2}),
        )
    )

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert [c.source_id for c in candidates] == ["2"]


@respx.mock
async def test_hereda_el_nsfw_de_la_comunidad(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    """Un post limpio en una comunidad NSFW sigue siendo NSFW."""
    respx.get(LIST_URL).mock(return_value=_payload(_view(community={"nsfw": True})))

    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, lemmy_config, client).discover(50)

    assert candidates[0].nsfw is True


# ---------------------------------------------------------------------------
# Errores y configuracion
# ---------------------------------------------------------------------------
@respx.mock
async def test_rate_limit_detiene_la_fuente(settings: Settings, lemmy_config: SourceConfig) -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(429))

    async with httpx.AsyncClient() as client:
        assert await LemmySource(settings, lemmy_config, client).discover(50) == []


@respx.mock
async def test_404_sin_comunidad_sugiere_que_la_instancia_ya_es_v4(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as client:
        source = LemmySource(settings, lemmy_config, client)
        with pytest.raises(SourceError, match="v4"):
            await source._fetch(None, "TopDay", 10)


@respx.mock
async def test_404_con_comunidad_habla_de_la_comunidad(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as client:
        source = LemmySource(settings, lemmy_config, client)
        with pytest.raises(SourceError, match="no existe"):
            await source._fetch("inventada@x.test", "TopDay", 10)


@respx.mock
async def test_una_comunidad_rota_no_tumba_la_fuente(settings: Settings) -> None:
    respx.get(LIST_URL, params={"community_name": "rota"}).mock(return_value=httpx.Response(404))
    respx.get(LIST_URL, params={"community_name": "buena"}).mock(return_value=_payload(_view()))

    config = SourceConfig.model_validate({"communities": ["rota", "buena"], "delay_seconds": 0})
    async with httpx.AsyncClient() as client:
        candidates = await LemmySource(settings, config, client).discover(50)

    assert len(candidates) == 1


@respx.mock
async def test_respuesta_sin_lista_de_posts(settings: Settings, lemmy_config: SourceConfig) -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, json={"error": "vaya"}))

    async with httpx.AsyncClient() as client:
        source = LemmySource(settings, lemmy_config, client)
        with pytest.raises(SourceError, match="lista `posts`"):
            await source._fetch(None, "TopDay", 10)


async def test_status_rechaza_un_sort_invalido(settings: Settings) -> None:
    """Lemmy 0.19 sustituyo `Top` por tokens compuestos como `TopDay`."""
    config = SourceConfig.model_validate({"sort": "Top"})

    async with httpx.AsyncClient() as client:
        status = await LemmySource(settings, config, client).status()

    assert not status.configured
    assert "TopDay" in status.detail


async def test_status_sin_comunidades_usa_la_portada(
    settings: Settings, lemmy_config: SourceConfig
) -> None:
    async with httpx.AsyncClient() as client:
        status = await LemmySource(settings, lemmy_config, client).status()

    assert status.configured
    assert "portada" in status.detail
    assert "sin credenciales" in status.detail


async def test_la_instancia_es_configurable(settings: Settings) -> None:
    config = SourceConfig.model_validate({"instance": "https://sh.itjust.works/"})

    async with httpx.AsyncClient() as client:
        source = LemmySource(settings, config, client)

    # Se normaliza la barra final para no generar URLs con doble barra.
    assert source.instance == "https://sh.itjust.works"
