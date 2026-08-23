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
from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.x import (
    SesionInvalidaError,
    XApiSource,
    XScrapeSource,
    build_x_source,
)

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


# ---------------------------------------------------------------------------
# El backend `scrape`, que va por la GraphQL interna de X
#
# Nada de esto toca la red: se sirve la portada, el bundle y las respuestas de
# la GraphQL con la forma real, comprobada contra X el 22/08/2026.
# ---------------------------------------------------------------------------
QUERY_USUARIO = "Gb-d6r0vxPOADdG62OEBpQ"
QUERY_MEDIOS = "VyudDWQnr9vJNw7GasFz2g"
BUNDLE_URL = "https://abs.twimg.com/responsive-web/client-web/main.dd6a5b6a.js"
GRAPHQL = "https://x.com/i/api/graphql"


@pytest.fixture
def cookies_de_x(tmp_path: Path) -> Path:
    """Un fichero Netscape con lo justo, mas ruido de otros dominios.

    El ruido no es decorativo: `--cookies-from-browser` exporta el perfil
    entero -incluida la sesion del correo de la cuenta- y hay una prueba
    especifica de que de ahi no sale nada que no sea de X.
    """
    ruta = tmp_path / "x.cookies.txt"
    ruta.write_text(
        "# Netscape HTTP Cookie File\n"
        ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tel-token-de-sesion\n"
        ".x.com\tTRUE\t/\tTRUE\t0\tct0\tel-csrf\n"
        ".twitter.com\tTRUE\t/\tTRUE\t0\tguest_id\tv1%3A123\n"
        ".outlook.live.com\tTRUE\t/\tTRUE\t0\tRPSSecAuth\tsesion-del-correo\n"
        ".doubleclick.net\tTRUE\t/\tTRUE\t0\tIDE\trastreador\n",
        encoding="utf-8",
    )
    return ruta


@pytest.fixture
def scrape_settings(x_settings: Settings, cookies_de_x: Path) -> Settings:
    return x_settings.model_copy(
        update={
            "x_backend": XBackend.SCRAPE,
            "enable_tos_risky_sources": True,
            "x_cookies_file": cookies_de_x,
        }
    )


def _cuentas(**extra: Any) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "weight": 0.7,
            "budget": 30,
            "accounts": ["uno", "dos", "tres"],
            "delay_seconds": 0,  # que los tests no esperen de verdad
            **extra,
        }
    )


def _tweet(id_str: str = "1800000000000000001", *, tipo: str = "video", **legacy: Any) -> dict:
    """Un item del timeline con la forma que devuelve `UserMedia`."""
    medio: dict[str, Any] = {
        "type": tipo,
        "media_url_https": "https://pbs.twimg.com/media/portada.jpg",
    }
    if tipo in ("video", "animated_gif"):
        medio["video_info"] = {"duration_millis": 12_233}

    datos: dict[str, Any] = {
        "id_str": id_str,
        "full_text": "mira esto https://t.co/VYG9yOoSum",
        "created_at": "Sun Aug 23 03:00:04 +0000 2026",
        "favorite_count": 178,
        "retweet_count": 22,
        "reply_count": 9,
        "possibly_sensitive": False,
        "lang": "es",
        "extended_entities": {"media": [medio]},
    }
    datos.update(legacy)
    return {
        "item": {
            "itemContent": {
                "tweet_results": {
                    "result": {
                        "legacy": datos,
                        "core": {"user_results": {"result": {"core": {"screen_name": "pepita"}}}},
                    }
                }
            }
        }
    }


def _timeline(*items: dict) -> dict:
    return {
        "data": {
            "user": {
                "result": {
                    "timeline": {
                        "timeline": {
                            "instructions": [
                                {"type": "TimelineClearCache"},
                                {
                                    "type": "TimelineAddEntries",
                                    "entries": [
                                        {
                                            "entryId": "profile-grid-0",
                                            "content": {"items": list(items)},
                                        }
                                    ],
                                },
                            ]
                        }
                    }
                }
            }
        }
    }


def _montar_descubrimiento() -> None:
    """La portada y el bundle, de donde salen los `queryId`."""
    respx.get("https://x.com/").mock(
        return_value=httpx.Response(200, html=f'<script src="{BUNDLE_URL}"></script>')
    )
    respx.get(BUNDLE_URL).mock(
        return_value=httpx.Response(
            200,
            text=(
                f'queryId:"{QUERY_USUARIO}",operationName:"UserByScreenName",'
                f'queryId:"{QUERY_MEDIOS}",operationName:"UserMedia",'
            ),
        )
    )


def _montar_usuario(rest_id: str = "536582046") -> None:
    respx.get(f"{GRAPHQL}/{QUERY_USUARIO}/UserByScreenName").mock(
        return_value=httpx.Response(200, json={"data": {"user": {"result": {"rest_id": rest_id}}}})
    )


def _fuente(settings: Settings, config: SourceConfig) -> XScrapeSource:
    return XScrapeSource(settings, config, httpx.AsyncClient())


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
async def test_sin_fichero_de_cookies_no_se_da_por_lista(
    scrape_settings: Settings, x_config: SourceConfig
) -> None:
    """X exige sesion: decir «lista» sin cookies era prometer lo que no hay."""
    sin_cookies = scrape_settings.model_copy(update={"x_cookies_file": None})

    estado = await (_fuente(sin_cookies, x_config)).status()

    assert not estado.configured
    assert "COOKIES" in estado.detail.upper()


async def test_unas_cookies_sin_csrf_se_avisan(
    scrape_settings: Settings, x_config: SourceConfig, tmp_path: Path
) -> None:
    """Sin `ct0` X responde 403 a todo, y eso hay que decirlo antes, no despues."""
    ruta = tmp_path / "medias.txt"
    ruta.write_text(
        "# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tsolo-esta\n",
        encoding="utf-8",
    )
    settings = scrape_settings.model_copy(update={"x_cookies_file": ruta})

    estado = await (_fuente(settings, x_config)).status()

    assert not estado.configured
    assert "ct0" in estado.detail


async def test_sin_el_flag_de_tos_sigue_bloqueada(
    scrape_settings: Settings, x_config: SourceConfig
) -> None:
    settings = scrape_settings.model_copy(update={"enable_tos_risky_sources": False})

    estado = await (_fuente(settings, x_config)).status()

    assert not estado.configured
    assert "ENABLE_TOS_RISKY_SOURCES" in estado.detail


# ---------------------------------------------------------------------------
# La sesion no se comparte de mas
# ---------------------------------------------------------------------------
async def test_solo_se_leen_las_cookies_de_x(
    scrape_settings: Settings, x_config: SourceConfig
) -> None:
    """El fichero trae el perfil entero del navegador; de aqui sale solo X.

    Es la diferencia entre mandarle a X sus propias cookies y mandarle tambien
    la sesion del correo con el que se registro la cuenta.
    """
    fuente = _fuente(scrape_settings, x_config)

    cookies = fuente._cookies()

    assert set(cookies) == {"auth_token", "ct0", "guest_id"}
    assert "RPSSecAuth" not in cookies


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------
@respx.mock
async def test_normaliza_un_tweet_con_video(scrape_settings: Settings) -> None:
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet()))
    )

    candidatos = await (_fuente(scrape_settings, _cuentas(objetivos_por_ronda=1))).discover(30)

    assert len(candidatos) == 1
    c = candidatos[0]
    assert c.source_id == "1800000000000000001"
    # La atribucion sale del tweet, no del perfil consultado.
    assert c.author == "pepita"
    assert c.permalink == "https://x.com/pepita/status/1800000000000000001"
    assert c.engagement == 200  # 178 likes + 22 rt
    assert c.comments == 9
    assert c.duration_seconds == pytest.approx(12.233)
    assert c.kind is MediaKind.VIDEO
    # Siempre via yt-dlp contra el permalink, que es quien sabe resolverlo.
    assert c.media_url is None
    # El `t.co` final apunta al propio video y en el titulo solo estorba.
    assert c.title == "mira esto"
    assert c.created_at.year == 2026


@respx.mock
async def test_los_gif_del_scrape_se_marcan_como_animacion(scrape_settings: Settings) -> None:
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet(tipo="animated_gif")))
    )

    candidatos = await (_fuente(scrape_settings, _cuentas(objetivos_por_ronda=1))).discover(30)

    assert candidatos[0].kind is MediaKind.ANIMATION


@respx.mock
async def test_se_descartan_las_fotos(scrape_settings: Settings) -> None:
    """La pestana de medios trae fotos, y de aqui solo sale lo publicable."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(
            200, json=_timeline(_tweet("1", tipo="photo"), _tweet("2", tipo="video"))
        )
    )

    candidatos = await (_fuente(scrape_settings, _cuentas(objetivos_por_ronda=1))).discover(30)

    assert [c.source_id for c in candidatos] == ["2"]


@respx.mock
async def test_solo_se_consulta_una_cuenta_por_ronda(scrape_settings: Settings) -> None:
    """Pedir las tres seguidas es justo lo que hace que te marquen."""
    _montar_descubrimiento()
    _montar_usuario()
    ruta = respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet()))
    )

    await (_fuente(scrape_settings, _cuentas(objetivos_por_ronda=1))).discover(30)

    assert ruta.call_count == 1


@respx.mock
async def test_el_id_de_usuario_se_cachea(scrape_settings: Settings) -> None:
    """No cambia nunca: resolverlo en cada ronda seria una peticion regalada."""
    _montar_descubrimiento()
    usuario = respx.get(f"{GRAPHQL}/{QUERY_USUARIO}/UserByScreenName").mock(
        return_value=httpx.Response(200, json={"data": {"user": {"result": {"rest_id": "1"}}}})
    )
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet()))
    )

    fuente = _fuente(scrape_settings, _cuentas(accounts=["uno"]))
    await fuente.discover(30)
    await fuente.discover(30)

    assert usuario.call_count == 1


@respx.mock
async def test_un_rate_limit_corta_la_ronda(scrape_settings: Settings) -> None:
    """Insistir cuando ya te estan limitando solo confirma el patron."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        side_effect=[
            httpx.Response(200, json=_timeline(_tweet("1"))),
            httpx.Response(429, headers={"x-rate-limit-reset": "0"}, json={}),
            httpx.Response(200, json=_timeline(_tweet("3"))),
        ]
    )

    fuente = _fuente(scrape_settings, _cuentas())
    candidatos = await fuente.discover(30)

    # La tercera cuenta no se llega a consultar, y lo recogido se conserva.
    assert [c.source_id for c in candidatos] == ["1"]


@respx.mock
async def test_una_cuenta_rota_no_tumba_las_demas(scrape_settings: Settings) -> None:
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        side_effect=[
            httpx.Response(404, json={}),
            httpx.Response(200, json=_timeline(_tweet("2"))),
            httpx.Response(200, json=_timeline(_tweet("3"))),
        ]
    )

    candidatos = await (_fuente(scrape_settings, _cuentas())).discover(30)

    assert [c.source_id for c in candidatos] == ["2", "3"]


@respx.mock
async def test_la_sesion_caducada_se_explica(scrape_settings: Settings) -> None:
    """Las cookies caducan, y «HTTP 403» a secas no le dice nada a nadie."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(return_value=httpx.Response(403, json={}))

    fuente = _fuente(scrape_settings, _cuentas(accounts=["uno"]))

    # Tipo propio: es el unico fallo de esta fuente que Scrappy puede arreglar
    # solo, y el mensaje dice como, no un «403» a secas.
    with pytest.raises(SesionInvalidaError, match="scrappy cookies"):
        await fuente._medios("uno", 10, fuente._cookies())


# ---------------------------------------------------------------------------
# Los queryId
# ---------------------------------------------------------------------------
@respx.mock
async def test_los_query_ids_salen_del_bundle(scrape_settings: Settings) -> None:
    """Hardcodearlos seria firmar que esto se rompa en una fecha desconocida."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet()))
    )

    fuente = _fuente(scrape_settings, _cuentas(accounts=["uno"]))
    await fuente.discover(30)

    assert fuente._queries == {
        "UserByScreenName": QUERY_USUARIO,
        "UserMedia": QUERY_MEDIOS,
    }


@respx.mock
async def test_los_query_ids_se_pueden_fijar_a_mano(scrape_settings: Settings) -> None:
    """La salida de emergencia si el bundle cambia de forma: sin tocar codigo."""
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        return_value=httpx.Response(200, json=_timeline(_tweet()))
    )
    portada = respx.get("https://x.com/").mock(return_value=httpx.Response(200, html=""))

    config = _cuentas(accounts=["uno"], query_id_user=QUERY_USUARIO, query_id_media=QUERY_MEDIOS)
    candidatos = await (_fuente(scrape_settings, config)).discover(30)

    assert len(candidatos) == 1
    # Con los ids puestos no hace falta ir a buscar el bundle.
    assert portada.call_count == 0


@respx.mock
async def test_sin_bundle_tambien_es_sesion_muerta(scrape_settings: Settings) -> None:
    """La sesion muerta se nota ANTES del 401, y ahi estaba el agujero.

    X solo referencia su bundle JS en la portada de una sesion iniciada; sin
    sesion sirve una pagina de aterrizaje. Como los `queryId` salen de ese
    bundle, la fuente moria con «no se encontro el bundle» -un error generico- y
    la renovacion automatica no llegaba a dispararse nunca.
    """
    respx.get("https://x.com/").mock(return_value=httpx.Response(200, html="<h1>Entra</h1>"))

    fuente = _fuente(scrape_settings, _cuentas(accounts=["uno"]))

    with pytest.raises(SesionInvalidaError):
        await fuente._query_ids(fuente._cookies())


@respx.mock
async def test_la_sesion_muerta_se_renueva_sola_y_se_reintenta(
    scrape_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto de todo esto: que no haga falta que nadie haga nada."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(
        side_effect=[
            httpx.Response(403, json={}),
            httpx.Response(200, json=_timeline(_tweet())),
        ]
    )

    renovaciones = []

    def _renovar_falso(_settings: Settings) -> Any:
        from scrappy.sources.x_cookies import RenovacionCookies

        renovaciones.append(1)
        return RenovacionCookies(True, "renovada", de_x=17)

    monkeypatch.setattr("scrappy.sources.x.renovar_cookies", _renovar_falso)

    candidatos = await _fuente(scrape_settings, _cuentas(accounts=["uno"])).discover(30)

    assert renovaciones == [1]
    assert len(candidatos) == 1


@respx.mock
async def test_no_se_renueva_dos_veces_en_la_misma_ronda(
    scrape_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insistir contra quien acaba de rechazarnos es lo que se lleva evitando."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(return_value=httpx.Response(403, json={}))

    renovaciones = []

    def _renovar_falso(_settings: Settings) -> Any:
        from scrappy.sources.x_cookies import RenovacionCookies

        renovaciones.append(1)
        return RenovacionCookies(True, "renovada", de_x=17)

    monkeypatch.setattr("scrappy.sources.x.renovar_cookies", _renovar_falso)

    with pytest.raises(SesionInvalidaError):
        await _fuente(scrape_settings, _cuentas()).discover(30)
    assert len(renovaciones) == 1


@respx.mock
async def test_si_el_navegador_tampoco_tiene_sesion_se_dice(
    scrape_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ahi si hace falta una persona, y el mensaje tiene que decir cual es."""
    _montar_descubrimiento()
    _montar_usuario()
    respx.get(f"{GRAPHQL}/{QUERY_MEDIOS}/UserMedia").mock(return_value=httpx.Response(403, json={}))

    def _renovar_falso(_settings: Settings) -> Any:
        from scrappy.sources.x_cookies import RenovacionCookies

        return RenovacionCookies(False, "no hay sesion de X: faltan auth_token")

    monkeypatch.setattr("scrappy.sources.x.renovar_cookies", _renovar_falso)

    fuente = _fuente(scrape_settings, _cuentas(accounts=["uno"]))
    with pytest.raises(SesionInvalidaError, match="auth_token"):
        await fuente._medios_con_renovacion("uno", 10, fuente._cookies())
