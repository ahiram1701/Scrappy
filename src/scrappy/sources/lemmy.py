"""Adapter de Lemmy.

Lemmy es la alternativa federada a Reddit y, a diferencia de este, expone una
API REST **publica, versionada y sin autenticacion** para leer contenido de
comunidades publicas: la lectura sin auth es un requisito de la federacion, no
una concesion. No hay claves que pedir, ni formularios de aprobacion, ni cuotas.

Encaja de forma natural con el resto del proyecto porque comparte el modelo de
datos de Reddit —comunidades, votos, comentarios—, asi que el ranking funciona
sin adaptaciones.

De hecho da mas informacion que los feeds Atom de Reddit:

- `counts.score` / `upvotes` / `comments`: engagement real, no derivado de la
  posicion.
- `url_content_type`: el tipo MIME lo dice el servidor, asi que no hay que
  adivinarlo por la extension.
- `featured_community` / `featured_local`: banderas de post fijado, asi que se
  descartan con precision en vez de por heuristica.
- `creator.bot_account`: permite saltarse los bots.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

_DEFAULT_INSTANCE = "https://lemmy.world"

# Lemmy 0.19 sustituyo el `Top` a secas por estos tokens compuestos.
_VALID_SORTS = frozenset(
    {
        "TopHour",
        "TopSixHour",
        "TopTwelveHour",
        "TopDay",
        "TopWeek",
        "TopMonth",
        "Hot",
        "Active",
        "New",
        "MostComments",
    }
)

_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
_VIDEO_TYPES = ("video/",)

_VIDEO_DOMAINS = ("youtube.com", "youtu.be", "streamable.com", "redgifs.com", "v.redd.it")

# La API no documenta un limite estricto, pero es una instancia mantenida por
# voluntarios: conviene no castigarla.
_DEFAULT_DELAY_SECONDS = 2.0


class LemmySource(SourceAdapter):
    """Descubre posts en las comunidades configuradas de una instancia de Lemmy."""

    name = "lemmy"
    requires_tos_ack = False

    @property
    def instance(self) -> str:
        return self.config.get_str("instance", _DEFAULT_INSTANCE).rstrip("/")

    async def status(self) -> SourceStatus:
        sort = self.config.get_str("sort", "TopDay")
        if sort not in _VALID_SORTS:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.lemmy_enabled,
                configured=False,
                detail=f"`sort: {sort}` no es valido. Opciones: {', '.join(sorted(_VALID_SORTS))}",
            )

        communities = self.config.get_list("communities")
        detalle = (
            f"{len(communities)} comunidades en {self.instance}"
            if communities
            else f"portada de {self.instance}"
        )
        return SourceStatus(
            name=self.name,
            enabled=self.settings.lemmy_enabled,
            configured=True,
            detail=f"{detalle}, sin credenciales",
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        communities = self.config.get_list("communities")
        sort = self.config.get_str("sort", "TopDay")
        delay = float(self.config.get_int("delay_seconds", int(_DEFAULT_DELAY_SECONDS)))

        # Sin comunidades configuradas se pide la portada de la instancia, que
        # ya viene agregada y cuesta una sola peticion.
        targets: list[str | None] = list(communities) if communities else [None]
        per_target = max(budget // len(targets), 10)

        candidates: list[RawCandidate] = []
        for index, community in enumerate(targets):
            if index:
                await asyncio.sleep(delay)
            try:
                views = await self._fetch(community, sort, per_target)
            except RateLimitedError:
                self.log.warning("rate_limited_stop", community=community)
                break
            except SourceError as exc:
                self.log.warning("community_failed", community=community, error=str(exc))
                continue

            for view in views:
                candidate = self._to_candidate(view)
                if candidate is not None:
                    candidates.append(candidate)

        self.log.info("discovered", count=len(candidates), targets=len(targets))
        return candidates

    async def _fetch(self, community: str | None, sort: str, limit: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "type_": "All",
            "sort": sort,
            "limit": min(limit, 50),
        }
        if community:
            params["community_name"] = community

        try:
            response = await self.client.get(f"{self.instance}/api/v3/post/list", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"{self.instance}: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitedError(self.name)
        if response.status_code == 404:
            # Puede ser la comunidad o, si la instancia ya solo sirve v4, el
            # endpoint entero. Merece la pena distinguirlo.
            detail = (
                f"la comunidad '{community}' no existe en {self.instance}"
                if community
                else f"{self.instance} no responde en /api/v3; puede que ya solo sirva v4"
            )
            raise SourceError(self.name, detail)
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code} en {self.instance}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(self.name, f"{self.instance} no devolvio JSON valido") from exc

        posts = payload.get("posts")
        if not isinstance(posts, list):
            raise SourceError(self.name, "la respuesta no contiene una lista `posts`")
        return [view for view in posts if isinstance(view, dict)]

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidate(self, view: dict[str, Any]) -> RawCandidate | None:
        post = view.get("post") or {}
        counts = view.get("counts") or {}
        creator = view.get("creator") or {}
        community = view.get("community") or {}

        if post.get("removed") or post.get("deleted"):
            return None
        # A diferencia de Reddit por RSS, aqui los posts fijados vienen marcados,
        # asi que se descartan con precision en vez de por heuristica.
        if post.get("featured_community") or post.get("featured_local"):
            return None
        if creator.get("bot_account"):
            return None

        post_id = post.get("id")
        url = post.get("url")
        if not post_id or not url:
            # Sin `url` es un post de solo texto: no hay nada que publicar.
            return None

        kind, media_url = self._classify(str(url), post.get("url_content_type"))
        if kind is None:
            return None

        author = str(creator.get("name") or "desconocido")

        return RawCandidate(
            source=self.name,
            source_id=str(post_id),
            # `ap_id` es la URL canonica en la instancia de origen, que es donde
            # esta el post de verdad y a donde debe apuntar la atribucion.
            permalink=str(post.get("ap_id") or f"{self.instance}/post/{post_id}"),
            title=str(post.get("name") or ""),
            author=author,
            author_url=creator.get("actor_id"),
            kind=kind,
            media_url=media_url,
            thumbnail_url=post.get("thumbnail_url"),
            duration_seconds=None,
            created_at=_parse_datetime(post.get("published")),
            engagement=max(int(counts.get("score") or 0), 0),
            comments=max(int(counts.get("comments") or 0), 0),
            nsfw=bool(post.get("nsfw") or community.get("nsfw")),
            language=None,
            extra={
                "community": community.get("name"),
                "instance": self.instance,
                "upvotes": counts.get("upvotes"),
            },
        )

    @staticmethod
    def _classify(url: str, content_type: str | None) -> tuple[MediaKind | None, str | None]:
        """Decide el tipo de medio.

        Se prefiere el `url_content_type` que informa el servidor a adivinar por
        la extension: es la ventaja de tener una API de verdad.
        """
        if content_type:
            lowered = content_type.lower()
            if lowered == "image/gif":
                return MediaKind.ANIMATION, url
            if lowered.startswith(_IMAGE_TYPES):
                return MediaKind.PHOTO, url
            if lowered.startswith(_VIDEO_TYPES):
                return MediaKind.VIDEO, url

        clean = url.lower().split("?")[0]
        if any(domain in clean for domain in _VIDEO_DOMAINS):
            # Enlaces a plataformas externas: los resuelve yt-dlp.
            return MediaKind.VIDEO, None
        if clean.endswith(".gif"):
            return MediaKind.ANIMATION, url
        if clean.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return MediaKind.PHOTO, url
        if clean.endswith((".mp4", ".webm")):
            return MediaKind.VIDEO, url

        # Enlaces a articulos y demas: no hay medio que publicar.
        return None, None


def _parse_datetime(value: Any) -> datetime:
    """Fecha ISO-8601 de Lemmy, que llega sin zona explicita pero es UTC."""
    if not isinstance(value, str):
        return utcnow()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
