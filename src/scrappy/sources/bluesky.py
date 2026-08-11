"""Adapter de Bluesky.

Es la respuesta del proyecto a "quiero pillar las tendencias antes de que
lleguen a las redes grandes". Bluesky es donde mucho contenido circula primero,
y su API es abierta: el AppView publico responde sin autenticacion.

## Dos trampas que hay que sortear

**El host.** La documentacion dice `public.api.bsky.app`, pero ese host
**devuelve 403 en las busquedas desde mediados de 2026**. El que funciona es
`api.bsky.app`. Comprobado, no deducido.

**La ventana temporal.** `sort=top` sin acotar devuelve lo mas votado de
*siempre*: en las pruebas salian posts de 2024 con 58.000 likes. Para que la
fuente sirva de senal temprana hay que acotarla con `since`, igual que el
`top/day` de Reddit.

## Limitacion conocida

Sin autenticar solo se puede leer la **primera pagina**: cualquier peticion con
`cursor` devuelve 403. Con el `budget` habitual (25-100 posts) basta de sobra,
asi que no se pagina en vez de fingir que se puede.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

# `public.api.bsky.app` da 403 en searchPosts desde mediados de 2026.
_HOST = "https://api.bsky.app"
_SEARCH_PATH = "/xrpc/app.bsky.feed.searchPosts"

_DEFAULT_WINDOW_HOURS = 24
_DEFAULT_DELAY_SECONDS = 1.0

# El AppView limita a 100 por peticion.
_MAX_LIMIT = 100


class BlueskySource(SourceAdapter):
    """Busca posts recientes con medio en las consultas configuradas."""

    name = "bluesky"
    requires_tos_ack = False

    async def status(self) -> SourceStatus:
        queries = self.config.get_list("queries")
        if not queries:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.bluesky_enabled,
                configured=False,
                detail="no hay `queries` en config/sources.yaml",
            )
        window = self.config.get_int("window_hours", _DEFAULT_WINDOW_HOURS)
        return SourceStatus(
            name=self.name,
            enabled=self.settings.bluesky_enabled,
            configured=True,
            detail=f"{len(queries)} consultas, ultimas {window}h, sin credenciales",
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        queries = self.config.get_list("queries")
        if not queries:
            return []

        window = self.config.get_int("window_hours", _DEFAULT_WINDOW_HOURS)
        delay = float(self.config.get_int("delay_seconds", int(_DEFAULT_DELAY_SECONDS)))
        per_query = min(max(budget // len(queries), 25), _MAX_LIMIT)
        since = utcnow() - timedelta(hours=window)

        candidates: list[RawCandidate] = []
        for index, query in enumerate(queries):
            if index:
                await asyncio.sleep(delay)
            try:
                posts = await self._search(query, per_query, since)
            except RateLimitedError:
                self.log.warning("rate_limited_stop", query=query)
                break
            except SourceError as exc:
                self.log.warning("query_failed", query=query, error=str(exc))
                continue

            for post in posts:
                candidate = self._to_candidate(post)
                if candidate is not None:
                    candidates.append(candidate)

        self.log.info("discovered", count=len(candidates), queries=len(queries))
        return candidates

    async def _search(self, query: str, limit: int, since: datetime) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "q": query,
            "limit": limit,
            "sort": "top",
            # Sin `since`, `sort=top` devuelve lo mas votado de siempre.
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        try:
            response = await self.client.get(f"{_HOST}{_SEARCH_PATH}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con Bluesky: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitedError(self.name)
        if response.status_code == 403:
            raise SourceError(
                self.name,
                "HTTP 403. Comprueba que se este usando api.bsky.app y no "
                "public.api.bsky.app, que rechaza las busquedas sin autenticar.",
            )
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(self.name, "la respuesta no es JSON valido") from exc

        posts = payload.get("posts")
        if not isinstance(posts, list):
            return []
        return [post for post in posts if isinstance(post, dict)]

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidate(self, post: dict[str, Any]) -> RawCandidate | None:
        uri = post.get("uri")
        author = post.get("author") or {}
        handle = author.get("handle")
        if not uri or not handle:
            return None

        kind, media_url = self._extract_media(post.get("embed") or {})
        if kind is None:
            return None

        # Un `at://` no es navegable. El permalink publico se compone del handle
        # y del `rkey`, que es el ultimo segmento del URI.
        rkey = str(uri).rsplit("/", 1)[-1]

        record = post.get("record") or {}
        labels = post.get("labels") or []

        return RawCandidate(
            source=self.name,
            source_id=rkey,
            permalink=f"https://bsky.app/profile/{handle}/post/{rkey}",
            title=str(record.get("text") or "")[:500],
            author=str(author.get("displayName") or handle),
            author_url=f"https://bsky.app/profile/{handle}",
            kind=kind,
            media_url=media_url,
            thumbnail_url=self._thumbnail(post.get("embed") or {}),
            duration_seconds=None,
            created_at=_parse_datetime(record.get("createdAt") or post.get("indexedAt")),
            # Los reposts son la senal de difusion mas directa que hay aqui, y
            # es justo lo que interesa para detectar algo que empieza a moverse.
            engagement=int(post.get("likeCount") or 0) + int(post.get("repostCount") or 0),
            comments=int(post.get("replyCount") or 0),
            nsfw=bool(labels),
            language=_first_language(record),
            extra={
                "likes": post.get("likeCount"),
                "reposts": post.get("repostCount"),
                "quotes": post.get("quoteCount"),
            },
        )

    @staticmethod
    def _unwrap(embed: dict[str, Any]) -> dict[str, Any]:
        """Desenvuelve `recordWithMedia`, que anida el medio real bajo `media`."""
        if embed.get("$type") == "app.bsky.embed.recordWithMedia#view":
            media = embed.get("media")
            return media if isinstance(media, dict) else {}
        return embed

    def _extract_media(self, embed: dict[str, Any]) -> tuple[MediaKind | None, str | None]:
        """Saca el medio del embed, si lo hay.

        Los posts de solo texto y las tarjetas de enlace se descartan: no hay
        nada que publicar en un canal de memes.
        """
        embed = self._unwrap(embed)
        embed_type = embed.get("$type")

        if embed_type == "app.bsky.embed.video#view":
            # El video se sirve como HLS (`playlist`, un .m3u8), que hay que
            # remuxear. Se delega en yt-dlp a partir del permalink.
            return MediaKind.VIDEO, None

        if embed_type == "app.bsky.embed.images#view":
            images = embed.get("images") or []
            if not images or not isinstance(images[0], dict):
                return None, None
            fullsize = images[0].get("fullsize")
            return (MediaKind.PHOTO, str(fullsize)) if fullsize else (None, None)

        return None, None

    def _thumbnail(self, embed: dict[str, Any]) -> str | None:
        embed = self._unwrap(embed)
        if embed.get("$type") == "app.bsky.embed.video#view":
            thumbnail = embed.get("thumbnail")
            return str(thumbnail) if thumbnail else None
        images = embed.get("images") or []
        if images and isinstance(images[0], dict) and images[0].get("thumb"):
            return str(images[0]["thumb"])
        return None


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return utcnow()


def _first_language(record: dict[str, Any]) -> str | None:
    langs = record.get("langs")
    if isinstance(langs, list) and langs:
        return str(langs[0])
    return None
