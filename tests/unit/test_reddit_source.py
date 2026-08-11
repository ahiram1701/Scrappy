"""Pruebas del adapter de Reddit sobre feeds Atom.

El fixture reproduce la estructura real del feed
`www.reddit.com/r/memes/.rss?sort=top&t=day`, incluida la tabla HTML escapada
del `<content>` de la que hay que extraer el enlace al medio.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import SourceError
from scrappy.core.models import MediaKind
from scrappy.sources.reddit import RedditSource

FEED_URL = "https://www.reddit.com/r/memes/.rss"


@pytest.fixture
def reddit_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"reddit_user_agent": "windows:scrappy:0.1.0 (by /u/pruebas)"}
    )


@pytest.fixture
def source_config() -> SourceConfig:
    # `delay_seconds: 0` para que los tests no esperen de verdad.
    return SourceConfig.model_validate(
        {
            "weight": 0.9,
            "budget": 60,
            "subreddits": ["memes"],
            "listing": "top",
            "delay_seconds": 0,
        }
    )


def _entry(
    *,
    post_id: str = "1r4jnof",
    title: str = "Just a friendly reminder",
    author: str = "Child_of_the_Abyss",
    media: str = "https://i.redd.it/wg37k34uggjg1.jpeg",
) -> str:
    """Una entrada con la forma exacta del feed real."""
    permalink = f"https://www.reddit.com/r/memes/comments/{post_id}/x/"
    content = (
        "&lt;table&gt; &lt;tr&gt;&lt;td&gt; "
        f"&lt;a href=&quot;{permalink}&quot;&gt;"
        "&lt;img src=&quot;https://preview.redd.it/x.jpeg&quot; /&gt;&lt;/a&gt; "
        "&lt;/td&gt;&lt;td&gt; submitted by "
        f"&lt;a href=&quot;https://www.reddit.com/user/{author}&quot;&gt;"
        f"/u/{author}&lt;/a&gt; &lt;br/&gt; "
        f"&lt;span&gt;&lt;a href=&quot;{media}&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; "
        f"&lt;span&gt;&lt;a href=&quot;{permalink}&quot;&gt;[comments]&lt;/a&gt;"
        "&lt;/span&gt; &lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"
    )
    return f"""<entry>
    <author>
      <name>/u/{author}</name>
      <uri>https://www.reddit.com/user/{author}</uri>
    </author>
    <category term="memes" label="r/memes"/>
    <content type="html">{content}</content>
    <id>t3_{post_id}</id>
    <media:thumbnail url="https://preview.redd.it/{post_id}.jpeg"/>
    <link href="{permalink}"/>
    <published>2026-08-11T09:12:00+00:00</published>
    <title>{title}</title>
    <updated>2026-08-11T09:12:00+00:00</updated>
  </entry>"""


def _feed(*entries: str) -> str:
    body = "\n".join(entries) if entries else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <category term="memes" label="r/memes"/>
  <updated>2026-08-11T12:09:23+00:00</updated>
  <id>/r/memes/.rss</id>
  {body}
</feed>"""


def _atom(body: str) -> httpx.Response:
    return httpx.Response(
        200, text=body, headers={"content-type": "application/atom+xml; charset=UTF-8"}
    )


# ---------------------------------------------------------------------------
# Normalizacion
# ---------------------------------------------------------------------------
@respx.mock
async def test_normaliza_una_entrada(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    respx.get(FEED_URL).mock(return_value=_atom(_feed(_entry())))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_id == "1r4jnof"  # sin el prefijo `t3_`
    assert candidate.author == "Child_of_the_Abyss"
    assert candidate.author_url == "https://www.reddit.com/user/Child_of_the_Abyss"
    assert candidate.title == "Just a friendly reminder"
    assert candidate.kind is MediaKind.PHOTO
    assert candidate.media_url == "https://i.redd.it/wg37k34uggjg1.jpeg"
    assert candidate.thumbnail_url is not None
    assert candidate.created_at.tzinfo is not None
    assert candidate.extra["subreddit"] == "memes"


@respx.mock
async def test_la_posicion_en_el_feed_es_el_engagement(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """El feed viene ordenado por score, asi que el primero debe puntuar mas.

    Es el sustituto de los upvotes, que el feed no informa.
    """
    respx.get(FEED_URL).mock(
        return_value=_atom(_feed(*(_entry(post_id=f"p{i}", title=f"Post {i}") for i in range(5))))
    )

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    engagements = [c.engagement for c in candidates]
    assert engagements == sorted(engagements, reverse=True)
    assert engagements[0] > engagements[-1]
    assert min(engagements) >= 1


@respx.mock
async def test_el_prefijo_del_autor_se_quita_sin_comerse_letras(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """El nombre viene como `/u/nombre` y hay que quitar el prefijo entero.

    Con `lstrip("/u/")` se quitaria *cualquiera* de esos caracteres, asi que un
    autor llamado "umberto" acabaria convertido en "mberto".
    """
    respx.get(FEED_URL).mock(return_value=_atom(_feed(_entry(author="umberto"))))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert candidates[0].author == "umberto"


@respx.mock
async def test_extrae_el_enlace_correcto_del_contenido(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """El `<content>` trae dos enlaces; hay que coger [link], no [comments]."""
    respx.get(FEED_URL).mock(return_value=_atom(_feed(_entry(media="https://i.redd.it/bueno.png"))))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert candidates[0].media_url == "https://i.redd.it/bueno.png"
    assert "comments" not in str(candidates[0].media_url)


@respx.mock
@pytest.mark.parametrize(
    ("media", "kind", "directo"),
    [
        ("https://i.redd.it/x.jpeg", MediaKind.PHOTO, True),
        ("https://i.redd.it/x.png", MediaKind.PHOTO, True),
        ("https://i.redd.it/x.gif", MediaKind.ANIMATION, True),
        # v.redd.it lleva el audio en pista aparte: se delega en yt-dlp.
        ("https://v.redd.it/abc123", MediaKind.VIDEO, False),
        ("https://redgifs.com/watch/algo", MediaKind.VIDEO, False),
    ],
)
async def test_clasifica_por_tipo_de_medio(
    reddit_settings: Settings,
    source_config: SourceConfig,
    media: str,
    kind: MediaKind,
    directo: bool,
) -> None:
    respx.get(FEED_URL).mock(return_value=_atom(_feed(_entry(media=media))))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert candidates[0].kind is kind
    assert (candidates[0].media_url is not None) is directo


@respx.mock
async def test_descarta_las_entradas_sin_medio(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Los posts de texto no traen enlace [link] en el contenido."""
    sin_medio = _entry().replace("[link]", "[texto]")
    respx.get(FEED_URL).mock(return_value=_atom(_feed(sin_medio, _entry(post_id="bueno"))))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert [c.source_id for c in candidates] == ["bueno"]


@respx.mock
async def test_los_posts_fijados_pierden_su_posicion_privilegiada(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Un anuncio fijado va primero en el feed sin merecerlo.

    Caso real observado contra Reddit: el puesto 0 lo ocupaba un post fijado de
    hace seis meses, y como aqui la posicion es el engagement, se llevaba la
    nota mas alta del lote.
    """
    fijado = _entry(post_id="fijado").replace(
        "<published>2026-08-11T09:12:00+00:00</published>",
        "<published>2026-02-14T09:12:00+00:00</published>",
    )
    recientes = [_entry(post_id=f"p{i}") for i in range(6)]
    respx.get(FEED_URL).mock(return_value=_atom(_feed(fijado, *recientes)))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    por_id = {c.source_id: c for c in candidates}
    assert por_id["fijado"].engagement == 1
    # Y no arrastra a los legitimos: el primero de verdad sigue arriba.
    assert por_id["p0"].engagement > por_id["fijado"].engagement


@respx.mock
async def test_no_demota_nada_en_un_feed_homogeneo(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """La heuristica no puede castigar a posts legitimos de edad parecida."""
    respx.get(FEED_URL).mock(
        return_value=_atom(_feed(*(_entry(post_id=f"p{i}") for i in range(6))))
    )

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    engagements = [c.engagement for c in candidates]
    assert engagements == sorted(engagements, reverse=True)
    assert len(set(engagements)) == len(engagements)


@respx.mock
async def test_feed_vacio(reddit_settings: Settings, source_config: SourceConfig) -> None:
    respx.get(FEED_URL).mock(return_value=_atom(_feed()))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert candidates == []


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
@respx.mock
async def test_rate_limit_detiene_la_fuente(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Ante un 429, insistir con el resto de subreddits solo empeora las cosas."""
    respx.get(FEED_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "60"}))

    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, source_config, client).discover(60)

    assert candidates == []


@respx.mock
async def test_403_explica_que_reddit_pudo_cerrar_los_feeds(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(403))

    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, source_config, client)
        with pytest.raises(SourceError, match="RSS"):
            await source._fetch_feed("memes", "top", "day")


@respx.mock
async def test_html_en_vez_de_feed_da_error_claro(
    reddit_settings: Settings, source_config: SourceConfig
) -> None:
    """Cuando Reddit bloquea, a veces devuelve HTML con un 200 enganoso."""
    respx.get(FEED_URL).mock(
        return_value=httpx.Response(200, text="<!DOCTYPE html><html>bloqueado</html>")
    )

    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, source_config, client)
        with pytest.raises(SourceError, match="no es un feed valido"):
            await source._fetch_feed("memes", "top", "day")


@respx.mock
async def test_un_subreddit_roto_no_tumba_la_fuente(reddit_settings: Settings) -> None:
    respx.get("https://www.reddit.com/r/privado/.rss").mock(return_value=httpx.Response(404))
    respx.get(FEED_URL).mock(return_value=_atom(_feed(_entry())))

    config = SourceConfig.model_validate(
        {"subreddits": ["privado", "memes"], "subreddits_per_run": 2, "delay_seconds": 0}
    )
    async with httpx.AsyncClient() as client:
        candidates = await RedditSource(reddit_settings, config, client).discover(60)

    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Rotacion y estado
# ---------------------------------------------------------------------------
async def test_rota_los_subreddits_para_no_agotar_el_rate_limit(
    reddit_settings: Settings,
) -> None:
    """No se consultan los 9 en cada ronda, sino 3 por vez."""
    config = SourceConfig.model_validate(
        {"subreddits": [f"sub{i}" for i in range(9)], "subreddits_per_run": 3}
    )
    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, config, client)
        seleccion = source._rotate(config.get_list("subreddits"))

    assert len(seleccion) == 3
    assert set(seleccion).issubset({f"sub{i}" for i in range(9)})


async def test_con_pocos_subreddits_se_consultan_todos(
    reddit_settings: Settings,
) -> None:
    config = SourceConfig.model_validate({"subreddits": ["a", "b"], "subreddits_per_run": 5})
    async with httpx.AsyncClient() as client:
        source = RedditSource(reddit_settings, config, client)
        assert source._rotate(["a", "b"]) == ["a", "b"]


async def test_status_exige_un_user_agent_identificable(
    settings: Settings, source_config: SourceConfig
) -> None:
    """Con un User-Agent generico Reddit responde 429, asi que se avisa antes."""
    generico = settings.model_copy(update={"reddit_user_agent": "python-requests/2.0"})

    async with httpx.AsyncClient() as client:
        status = await RedditSource(generico, source_config, client).status()

    assert not status.configured
    assert "USER_AGENT" in status.detail


async def test_status_correcto(reddit_settings: Settings, source_config: SourceConfig) -> None:
    async with httpx.AsyncClient() as client:
        status = await RedditSource(reddit_settings, source_config, client).status()

    assert status.configured
    assert "RSS" in status.detail


async def test_status_sin_subreddits(reddit_settings: Settings) -> None:
    async with httpx.AsyncClient() as client:
        status = await RedditSource(reddit_settings, SourceConfig(), client).status()

    assert not status.configured
    assert "subreddits" in status.detail
