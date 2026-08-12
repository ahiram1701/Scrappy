"""Adapter de Imgur.

Imgur mantiene el registro autoservicio de aplicaciones, que es justo lo que
Reddit cerro: se saca un Client-ID al momento en
https://api.imgur.com/oauth2/addclient y da unas 12.500 peticiones al dia.
Solo hace falta el Client-ID, no el secreto: para leer la galeria publica basta
con la autenticacion anonima.

Es una fuente de mucho volumen y bastante upstream: muchos memes se suben aqui
antes de circular por otras plataformas.

NOTA: a diferencia de Reddit, Lemmy y Bluesky, este adapter no se ha podido
verificar contra la API real porque hace falta una clave. Esta construido sobre
el formato documentado de la API v3 y probado con fixtures. Si al activarlo algo
no cuadra, es el primer sitio donde mirar.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

_API = "https://api.imgur.com/3"
_DEFAULT_DELAY_SECONDS = 1.0


class ImgurSource(SourceAdapter):
    """Descubre contenido en la galeria viral y en las etiquetas configuradas."""

    name = "imgur"
    requires_tos_ack = False

    async def status(self) -> SourceStatus:
        if not self.settings.imgur_client_id.get_secret_value():
            return SourceStatus(
                name=self.name,
                enabled=self.settings.imgur_enabled,
                configured=False,
                detail=(
                    "falta SCRAPPY_IMGUR_CLIENT_ID. Se saca al momento en "
                    "https://api.imgur.com/oauth2/addclient (solo el Client-ID, "
                    "el secreto no hace falta)"
                ),
            )
        tags = self.config.get_list("tags")
        detalle = f"galeria viral + {len(tags)} etiquetas" if tags else "galeria viral"
        return SourceStatus(
            name=self.name,
            enabled=self.settings.imgur_enabled,
            configured=True,
            detail=detalle,
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        window = self.config.get_str("window", "day")
        delay = float(self.config.get_int("delay_seconds", int(_DEFAULT_DELAY_SECONDS)))

        # La galeria viral siempre, mas las etiquetas que se hayan configurado.
        paths = [f"/gallery/hot/viral/{window}/0"]
        paths += [f"/gallery/t/{tag}/top/{window}/0" for tag in self.config.get_list("tags")]

        candidates: list[RawCandidate] = []
        for index, path in enumerate(paths):
            if index:
                await asyncio.sleep(delay)
            try:
                items = await self._fetch(path)
            except RateLimitedError:
                self.log.warning("rate_limited_stop", path=path)
                break
            except SourceError as exc:
                self.log.warning("path_failed", path=path, error=str(exc))
                continue

            for item in items:
                candidate = self._to_candidate(item)
                if candidate is not None:
                    candidates.append(candidate)

        self.log.info("discovered", count=len(candidates), paths=len(paths))
        return candidates[:budget] if budget else candidates

    async def _fetch(self, path: str) -> list[dict[str, Any]]:
        client_id = self.settings.imgur_client_id.get_secret_value()
        try:
            response = await self.client.get(
                f"{_API}{path}",
                headers={"Authorization": f"Client-ID {client_id}"},
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con Imgur: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitedError(self.name)
        if response.status_code == 403:
            raise SourceError(
                self.name,
                "HTTP 403: Client-ID invalido, o la aplicacion ha superado su cuota "
                "diaria (unas 12.500 peticiones)",
            )
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(self.name, "la respuesta no es JSON valido") from exc

        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidate(self, item: dict[str, Any]) -> RawCandidate | None:
        item_id = item.get("id")
        if not item_id:
            return None

        media = self._media_of(item)
        if media is None:
            return None
        kind, media_url = media

        author = str(item.get("account_url") or "desconocido")

        return RawCandidate(
            source=self.name,
            source_id=str(item_id),
            permalink=str(item.get("link") or f"https://imgur.com/gallery/{item_id}"),
            title=str(item.get("title") or ""),
            author=author,
            author_url=(f"https://imgur.com/user/{author}" if author != "desconocido" else None),
            kind=kind,
            media_url=media_url,
            thumbnail_url=f"https://i.imgur.com/{item_id}m.jpg",
            duration_seconds=None,
            created_at=_from_epoch(item.get("datetime")),
            engagement=max(int(item.get("ups") or item.get("score") or 0), 0),
            comments=max(int(item.get("comment_count") or 0), 0),
            nsfw=bool(item.get("nsfw")),
            language=None,
            extra={"views": item.get("views"), "is_album": item.get("is_album")},
        )

    @staticmethod
    def _media_of(item: dict[str, Any]) -> tuple[MediaKind, str] | None:
        """Saca el medio publicable, desenvolviendo los albumes.

        Un album trae varias imagenes; se coge la primera, que es la portada y
        casi siempre la que da sentido al post.
        """
        source = item
        if item.get("is_album"):
            images = item.get("images") or []
            if not images or not isinstance(images[0], dict):
                return None
            source = images[0]

        # Los GIF se sirven tambien como mp4, que pesa una fraccion y Telegram
        # reproduce mejor. Se prefiere siempre.
        if source.get("animated"):
            mp4 = source.get("mp4")
            if mp4:
                return MediaKind.ANIMATION, str(mp4)

        link = source.get("link")
        if not link:
            return None

        mime = str(source.get("type") or "").lower()
        if mime.startswith("video/"):
            return MediaKind.VIDEO, str(link)
        if mime == "image/gif":
            return MediaKind.ANIMATION, str(link)
        if mime.startswith("image/"):
            return MediaKind.PHOTO, str(link)
        return None


def _from_epoch(value: Any) -> datetime:
    """Imgur informa la fecha como epoch en segundos."""
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return utcnow()
