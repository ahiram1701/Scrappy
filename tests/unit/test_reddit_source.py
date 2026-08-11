"""Pruebas del adapter de Reddit.

Ninguna llega a la red: `respx` intercepta httpx y devuelve payloads reales
recortados. Lo que se verifica es la normalizacion, que es donde estan los
casos raros: posts de texto, galerias, videos DASH sin audio en la URL directa…
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.reddit import RedditSource

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
LISTING_URL = "https://oauth.reddit.com/r/memes/top"


@pytest.fixture
def reddit_settings(settings: Settings) -> Settings:
    # `model_copy` no valida, asi que los secretos se pasan ya envueltos.
    return settings.model_copy(
        update={
            "reddit_client_id": SecretStr("id-de-prueba"),
            "reddit_client_secret": SecretStr("secreto-de-prueba"),
        }
    )


@pytest.fixture
def source_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {"weight": 0.9, "budget": 20, "subreddits": ["memes"], "listing": "top"}
    )


def _post(**overrides: Any) -> dict[str, Any]:
    """Post de Reddit con la forma real, recortado a lo que usamos."""
    base = {
        "id": "abc123",
        "title": "Un meme",
        "author": "pepita",
        "permalink": "/r/memes/comments/abc123/un_meme/",
        "url": "https://i.redd.it/abc123.jpg",
        "domain": "i.redd.it",
        "post_hint": "image",
        "created_utc": 1_754_900_000,
        "score": 12_345,
        "num_comments": 400,
        "over_18": False,
        "thumbnail": "https://b.thumbs.redditmedia.com/x.jpg",
        "is_self": False,
        "stickied": False,
    }
    base.update(overrides)
    return base


def _listing(*posts: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"children": [{"kind": "t3", "data": post} for post in posts]}}


@respx.mock
async def test_descubre_y_normaliza(reddit_settings: Settings, source_config: SourceConfig) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, json=_listing(_post())))

    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, source_config, client)
        candidates = await source.discover(20)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "reddit"
    assert candidate.source_id == "abc123"
    assert candidate.author == "pepita"
    assert candidate.engagement == 12_345
    assert candidate.comments == 400
    assert candidate.kind is MediaKind.PHOTO
    assert candidate.permalink.startswith("https://www.reddit.com/r/memes/")
    assert candidate.created_at.tzinfo is not None


@respx.mock
async def test_el_token_se_reutiliza(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Pedir un token por cada subreddit seria tirar cuota a la basura."""
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, json=_listing(_post())))

    config = SourceConfig.model_validate({"subreddits": ["memes", "memes"]})
    async with httpx.AsyncClient() as client:
        await RedditSource(reddit_settings, config, client).discover(20)

    assert token_route.call_count == 1


@respx.mock
async def test_credenciales_invalidas(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401))

    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, source_config, client)
        with pytest.raises(SourceError, match="credenciales rechazadas"):
            await source.discover(20)


@respx.mock
async def test_rate_limit_se_propaga(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Un 429 debe frenar la fuente, no reintentarse en bucle."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "120"}))

    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, source_config, client)
        with pytest.raises(RateLimitedError) as exc_info:
            await source.discover(20)

    assert exc_info.value.retry_after_seconds == 120


@respx.mock
async def test_un_subreddit_roto_no_tumba_la_fuente(reddit_settings: Settings) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get("https://oauth.reddit.com/r/privado/top").mock(return_value=httpx.Response(403))
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, json=_listing(_post())))

    config = SourceConfig.model_validate({"subreddits": ["privado", "memes"]})
    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, config, client).discover(20)

    assert len(candidates) == 1


@respx.mock
async def test_descarta_lo_que_no_tiene_medio(reddit_settings: Settings) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(
        return_value=httpx.Response(
            200,
            json=_listing(
                _post(id="texto", is_self=True),
                _post(id="fijado", stickied=True),
                _post(
                    id="articulo",
                    url="https://noticias.test/algo",
                    domain="noticias.test",
                    post_hint="link",
                ),
                _post(id="bueno"),
            ),
        )
    )

    config = SourceConfig.model_validate({"subreddits": ["memes"]})
    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, config, client).discover(20)

    assert [c.source_id for c in candidates] == ["bueno"]


@respx.mock
async def test_el_video_de_reddit_se_deja_a_ytdlp(reddit_settings: Settings) -> None:
    """El audio va en una pista DASH aparte: la URL directa seria muda."""
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(
        return_value=httpx.Response(
            200,
            json=_listing(
                _post(
                    id="video",
                    is_video=True,
                    url="https://v.redd.it/video",
                    domain="v.redd.it",
                    post_hint="hosted:video",
                    media={"reddit_video": {"duration": 42}},
                )
            ),
        )
    )

    config = SourceConfig.model_validate({"subreddits": ["memes"]})
    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, config, client).discover(20)

    assert candidates[0].kind is MediaKind.VIDEO
    assert candidates[0].media_url is None
    assert candidates[0].duration_seconds == 42


@respx.mock
async def test_respeta_el_min_score_de_la_configuracion(reddit_settings: Settings) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(LISTING_URL).mock(
        return_value=httpx.Response(
            200,
            json=_listing(_post(id="flojo", score=10), _post(id="fuerte", score=9_000)),
        )
    )

    config = SourceConfig.model_validate({"subreddits": ["memes"], "min_score": 500})
    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, config, client).discover(20)

    assert [c.source_id for c in candidates] == ["fuerte"]


async def test_status_avisa_de_lo_que_falta(
    settings: Settings, source_config: SourceConfig
) -> None:
    async with httpx.AsyncClient() as client:
        sin_credenciales = await RedditSource(settings, source_config, client).status()
        assert not sin_credenciales.configured
        assert "CLIENT_ID" in sin_credenciales.detail

        con_credenciales = settings.model_copy(
            update={
                "reddit_client_id": SecretStr("a"),
                "reddit_client_secret": SecretStr("b"),
            }
        )
        sin_subreddits = await RedditSource(con_credenciales, SourceConfig(), client).status()
        assert not sin_subreddits.configured
        assert "subreddits" in sin_subreddits.detail
