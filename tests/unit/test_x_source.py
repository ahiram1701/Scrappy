"""Pruebas del adapter de X.

Dos cosas concretas que verificar aqui:

  1. Que la reconstruccion de tweets a partir de `data` + `includes` funcione;
     la API v2 devuelve autores y medios en bloques aparte y hay que unirlos.
  2. Que el 403 del tier gratuito de la busqueda se explique con claridad. Es
     el error que se va a encontrar la mayoria de la gente que active X, y un
     "HTTP 403" a secas no le diria nada.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings, XBackend
from scrappy.core.errors import DownloadError, RateLimitedError, SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.x import XApiSource, XScrapeSource, build_x_source

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


@pytest.fixture
def x_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"x_enabled": True, "x_bearer_token": SecretStr("bearer-de-prueba")}
    )


@pytest.fixture
def x_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {"weight": 0.7, "budget": 20, "queries": ["meme has:videos"], "accounts": ["Memes"]}
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    """Respuesta de la API v2 con la forma real: data + includes expandidas."""
    base: dict[str, Any] = {
        "data": [
            {
                "id": "1800000000000000001",
                "text": "esto es un meme",
                "author_id": "u1",
                "created_at": "2026-08-11T09:00:00.000Z",
                "lang": "es",
                "possibly_sensitive": False,
                "public_metrics": {
                    "like_count": 5_000,
                    "retweet_count": 900,
                    "reply_count": 120,
                },
                "attachments": {"media_keys": ["m1"]},
            }
        ],
        "includes": {
            "users": [{"id": "u1", "username": "pepita", "name": "Pepita"}],
            "media": [
                {
                    "media_key": "m1",
                    "type": "video",
                    "duration_ms": 15_000,
                    "preview_image_url": "https://pbs.test/preview.jpg",
                }
            ],
        },
    }
    base.update(overrides)
    return base


@respx.mock
async def test_reconstruye_el_tweet_desde_las_expansiones(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=_payload()))

    async with httpx.AsyncClient() as client:
        candidates = await XApiSource(x_settings, x_config, client).discover(20)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.author == "pepita"
    assert candidate.permalink == "https://x.com/pepita/status/1800000000000000001"
    # El engagement suma likes y retweets: las dos son senales de difusion.
    assert candidate.engagement == 5_900
    assert candidate.comments == 120
    assert candidate.duration_seconds == 15.0
    assert candidate.kind is MediaKind.VIDEO
    assert candidate.language == "es"


@respx.mock
async def test_los_gif_animados_se_marcan_como_animacion(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    payload = _payload()
    payload["includes"]["media"][0]["type"] = "animated_gif"
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    async with httpx.AsyncClient() as client:
        candidates = await XApiSource(x_settings, x_config, client).discover(20)

    assert candidates[0].kind is MediaKind.ANIMATION


@respx.mock
async def test_se_descartan_los_tweets_sin_video(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    """Sin medio no hay nada que publicar; el texto suelto no interesa."""
    payload = _payload()
    payload["includes"]["media"] = []
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    async with httpx.AsyncClient() as client:
        candidates = await XApiSource(x_settings, x_config, client).discover(20)

    assert candidates == []


@respx.mock
async def test_el_403_del_tier_gratuito_se_explica(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    """El error que se encontrara casi todo el mundo al activar X."""
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))

    async with httpx.AsyncClient() as client:
        candidates = await XApiSource(x_settings, x_config, client).discover(20)

    # Una query fallida no tumba la fuente, pero el motivo queda en el log.
    assert candidates == []


@respx.mock
async def test_el_403_como_unica_query_deja_la_fuente_vacia(
    x_settings: Settings,
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))
    config = SourceConfig.model_validate({"queries": ["a"]})

    async with httpx.AsyncClient() as client:
        source = XApiSource(x_settings, config, client)
        with pytest.raises(SourceError, match="tier Basic"):
            await source._search("a", 10)


@respx.mock
async def test_rate_limit(x_settings: Settings, x_config: SourceConfig) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(429, headers={"x-rate-limit-reset": "99999999999"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RateLimitedError):
            await XApiSource(x_settings, x_config, client).discover(20)


async def test_status_sin_token(settings: Settings, x_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await XApiSource(settings, x_config, client).status()
    assert not status.configured
    assert "BEARER_TOKEN" in status.detail


async def test_status_avisa_del_coste(x_settings: Settings, x_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await XApiSource(x_settings, x_config, client).status()
    assert status.configured
    assert "tier Basic" in status.detail


# ---------------------------------------------------------------------------
# Eleccion de backend
# ---------------------------------------------------------------------------
async def test_la_factoria_elige_el_backend(x_settings: Settings, x_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        api = build_x_source(x_settings, x_config, client)
        scrape = build_x_source(
            x_settings.model_copy(update={"x_backend": XBackend.SCRAPE}), x_config, client
        )

    assert isinstance(api, XApiSource)
    assert isinstance(scrape, XScrapeSource)


async def test_el_backend_de_scraping_exige_consentimiento(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    """Sin el flag explicito, la via que incumple los ToS no se activa sola."""
    from scrappy.core.errors import ToSAcknowledgementRequiredError

    scrape_settings = x_settings.model_copy(
        update={"x_backend": XBackend.SCRAPE, "enable_tos_risky_sources": False}
    )

    async with httpx.AsyncClient() as client:
        source = XScrapeSource(scrape_settings, x_config, client)
        with pytest.raises(ToSAcknowledgementRequiredError, match="LEGAL"):
            await source.discover(10)


async def test_el_backend_de_scraping_construye_las_urls(
    x_settings: Settings, x_config: SourceConfig
) -> None:
    async with httpx.AsyncClient() as client:
        source = XScrapeSource(x_settings, x_config, client)

    assert source.collection_urls() == ["https://x.com/Memes"]
    assert source.permalink_for({"id": "123", "uploader": "pepita"}) == (
        "https://x.com/pepita/status/123"
    )


# ---------------------------------------------------------------------------
# El backend de scraping, y lo que evita que salga caro
# ---------------------------------------------------------------------------
@pytest.fixture
def scrape_settings(x_settings: Settings, tmp_path: Path) -> Settings:
    """Ajustes con el scraping ya consentido y unas cookies que existen."""
    cookies = tmp_path / "x.cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return x_settings.model_copy(
        update={
            "x_backend": XBackend.SCRAPE,
            "enable_tos_risky_sources": True,
            "x_cookies_file": cookies,
        }
    )


def _perfiles(**extra: Any) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "weight": 0.7,
            "budget": 30,
            "accounts": ["uno", "dos", "tres"],
            "delay_seconds": 0,  # que los tests no esperen de verdad
            **extra,
        }
    )


class _EngineFalso:
    """Sustituto de `YtDlpEngine` que apunta a quien se le pregunta."""

    def __init__(self, respuestas: dict[str, Any] | None = None) -> None:
        self.consultadas: list[str] = []
        self._respuestas = respuestas or {}

    async def enumerate(self, url: str, **_kwargs: Any) -> list[dict[str, Any]]:
        self.consultadas.append(url)
        respuesta = self._respuestas.get(url, [])
        if isinstance(respuesta, Exception):
            raise respuesta
        return list(respuesta)


async def test_sin_cookies_no_se_da_por_lista(
    scrape_settings: Settings, x_config: SourceConfig
) -> None:
    """X exige sesion: decir «lista» sin cookies era prometer lo que no hay.

    El fallo no se veia hasta la ronda siguiente, en un log que nadie mira.
    """
    sin_cookies = scrape_settings.model_copy(update={"x_cookies_file": None})

    async with httpx.AsyncClient() as client:
        estado = await XScrapeSource(sin_cookies, x_config, client).status()

    assert not estado.configured
    assert "cookies" in estado.detail


async def test_solo_se_consulta_un_perfil_por_ronda(scrape_settings: Settings) -> None:
    """Pedir los tres perfiles seguidos es justo lo que hace que te marquen."""
    engine = _EngineFalso()

    async with httpx.AsyncClient() as client:
        source = XScrapeSource(scrape_settings, _perfiles(objetivos_por_ronda=1), client)
        source._engine = engine  # type: ignore[assignment]
        await source.discover(30)

    assert len(engine.consultadas) == 1
    assert engine.consultadas[0] in [f"https://x.com/{n}" for n in ("uno", "dos", "tres")]


async def test_un_rate_limit_corta_la_ronda(scrape_settings: Settings) -> None:
    """Insistir con el resto de la lista solo confirma el patron que delata."""
    engine = _EngineFalso(
        {
            "https://x.com/uno": [{"id": "1", "uploader": "uno"}],
            "https://x.com/dos": DownloadError("HTTP Error 429: Too Many Requests"),
        }
    )

    async with httpx.AsyncClient() as client:
        source = XScrapeSource(scrape_settings, _perfiles(), client)
        source._engine = engine  # type: ignore[assignment]
        candidatos = await source.discover(30)

    assert engine.consultadas == ["https://x.com/uno", "https://x.com/dos"]
    # Lo que ya se habia recogido no se tira: parar no es fracasar.
    assert [c.source_id for c in candidatos] == ["1"]


async def test_un_fallo_normal_no_corta_la_ronda(scrape_settings: Settings) -> None:
    """Un perfil borrado o privado no puede dejar sin ronda a los demas."""
    engine = _EngineFalso(
        {
            "https://x.com/uno": DownloadError("Unable to find profile"),
            "https://x.com/tres": [{"id": "3", "uploader": "tres"}],
        }
    )

    async with httpx.AsyncClient() as client:
        source = XScrapeSource(scrape_settings, _perfiles(), client)
        source._engine = engine  # type: ignore[assignment]
        candidatos = await source.discover(30)

    assert len(engine.consultadas) == 3
    assert [c.source_id for c in candidatos] == ["3"]


async def test_sin_cookies_ni_se_intenta(scrape_settings: Settings) -> None:
    """Gastar una peticion en confirmar que falta la sesion no ayuda a nadie.

    Sin esto, el scheduler la intentaba cada cuatro horas para siempre.
    """
    engine = _EngineFalso()
    sin_cookies = scrape_settings.model_copy(update={"x_cookies_file": None})

    async with httpx.AsyncClient() as client:
        source = XScrapeSource(sin_cookies, _perfiles(), client)
        source._engine = engine  # type: ignore[assignment]
        assert await source.discover(30) == []

    assert engine.consultadas == []
