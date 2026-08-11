"""Adapter de X (Twitter), con dos backends intercambiables.

Es la fuente con la situacion mas incomoda del proyecto, y conviene decirlo sin
rodeos:

    backend `api`     Usa la API oficial v2 (`/2/tweets/search/recent`). Es la
                      via limpia, pero el tier gratuito de X NO permite buscar
                      posts: el endpoint de busqueda requiere el tier Basic,
                      que en el momento de escribir esto ronda los 200 USD al
                      mes. Con el tier gratuito este backend devolvera 403.

    backend `scrape`  Enumera perfiles con yt-dlp y cookies. No cuesta dinero,
                      pero incumple los terminos de servicio de X, se rompe
                      cuando la plataforma cambia, y puede acarrear el bloqueo
                      de la cuenta cuyas cookies se usen.

Se elige con `SCRAPPY_X_BACKEND`. El backend `scrape` ademas exige activar
`SCRAPPY_ENABLE_TOS_RISKY_SOURCES`. Ver `docs/LEGAL.md` y
`docs/adr/0003-x-doble-backend.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from scrappy.config.settings import XBackend
from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus
from scrappy.sources.ytdlp_base import YtDlpSource

_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

# Campos minimos para poder rankear y atribuir. Pedir de mas gasta cuota.
_TWEET_FIELDS = "created_at,public_metrics,lang,possibly_sensitive,attachments,author_id"
_EXPANSIONS = "author_id,attachments.media_keys"
_USER_FIELDS = "username,name"
_MEDIA_FIELDS = "type,duration_ms,preview_image_url,public_metrics"


class XApiSource(SourceAdapter):
    """Backend oficial: busqueda reciente de la API v2."""

    name = "x"
    requires_tos_ack = False  # la API oficial es un uso permitido... si se paga

    async def status(self) -> SourceStatus:
        token = self.settings.x_bearer_token.get_secret_value()
        if not token:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.x_enabled,
                configured=False,
                detail="falta SCRAPPY_X_BEARER_TOKEN",
            )
        if not self.config.get_list("queries"):
            return SourceStatus(
                name=self.name,
                enabled=self.settings.x_enabled,
                configured=False,
                detail="no hay `queries` en config/sources.yaml",
            )
        return SourceStatus(
            name=self.name,
            enabled=self.settings.x_enabled,
            configured=True,
            detail="backend api (requiere tier Basic o superior para buscar)",
        )

    async def discover(self, budget: int) -> list[RawCandidate]:
        queries = self.config.get_list("queries")
        if not queries:
            return []

        per_query = max(budget // len(queries), 10)
        candidates: list[RawCandidate] = []

        for query in queries:
            try:
                payload = await self._search(query, per_query)
            except RateLimitedError:
                raise
            except SourceError as exc:
                self.log.warning("query_failed", query=query, error=str(exc))
                continue
            candidates.extend(self._parse(payload))

        self.log.info("discovered", count=len(candidates), queries=len(queries))
        return candidates

    async def _search(self, query: str, limit: int) -> dict[str, Any]:
        token = self.settings.x_bearer_token.get_secret_value()
        params: dict[str, str | int] = {
            "query": query,
            "max_results": min(max(limit, 10), 100),
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "media.fields": _MEDIA_FIELDS,
        }
        try:
            response = await self.client.get(
                _SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con la API de X: {exc}") from exc

        if response.status_code == 429:
            reset = response.headers.get("x-rate-limit-reset")
            retry_after: float | None = None
            if reset:
                with_suppress = utcnow().timestamp()
                retry_after = max(float(reset) - with_suppress, 0.0)
            raise RateLimitedError(self.name, retry_after)

        if response.status_code == 403:
            raise SourceError(
                self.name,
                "HTTP 403. El endpoint de busqueda de la API de X no esta incluido "
                "en el tier gratuito; hace falta el tier Basic o superior. "
                "Alternativa: SCRAPPY_X_BACKEND=scrape (lee docs/LEGAL.md antes).",
            )
        if response.status_code == 401:
            raise SourceError(self.name, "bearer token invalido o caducado")
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code}: {response.text[:200]}")

        return dict(response.json())

    def _parse(self, payload: dict[str, Any]) -> list[RawCandidate]:
        """Reconstruye los tweets uniendo `data` con las `includes` expandidas."""
        tweets = payload.get("data") or []
        includes = payload.get("includes") or {}
        users = {user["id"]: user for user in includes.get("users", [])}
        media = {item["media_key"]: item for item in includes.get("media", [])}

        candidates: list[RawCandidate] = []
        for tweet in tweets:
            media_keys = (tweet.get("attachments") or {}).get("media_keys") or []
            attached = [media[key] for key in media_keys if key in media]
            videos = [item for item in attached if item.get("type") in {"video", "animated_gif"}]
            if not videos:
                # La query pide has:videos, pero si la expansion no llego no se
                # puede saber que descargar. Mejor saltarlo que publicar texto.
                continue

            first = videos[0]
            user = users.get(tweet.get("author_id", ""), {})
            username = user.get("username", "desconocido")
            metrics = tweet.get("public_metrics") or {}

            duration_ms = first.get("duration_ms")
            candidates.append(
                RawCandidate(
                    source=self.name,
                    source_id=str(tweet["id"]),
                    permalink=f"https://x.com/{username}/status/{tweet['id']}",
                    title=str(tweet.get("text") or "")[:500],
                    author=username,
                    author_url=f"https://x.com/{username}",
                    kind=(
                        MediaKind.ANIMATION
                        if first.get("type") == "animated_gif"
                        else MediaKind.VIDEO
                    ),
                    media_url=None,
                    thumbnail_url=first.get("preview_image_url"),
                    duration_seconds=float(duration_ms) / 1000 if duration_ms else None,
                    created_at=_parse_iso(tweet.get("created_at")),
                    engagement=int(metrics.get("like_count", 0))
                    + int(metrics.get("retweet_count", 0)),
                    comments=int(metrics.get("reply_count", 0)),
                    nsfw=bool(tweet.get("possibly_sensitive")),
                    language=tweet.get("lang"),
                    extra={"backend": "api"},
                )
            )
        return candidates


class XScrapeSource(YtDlpSource):
    """Backend de scraping: enumera perfiles con yt-dlp.

    Gratis pero contra los terminos de X. Requiere consentimiento explicito.
    """

    name = "x"
    platform_label = "X / Twitter"
    cookies_required = False

    def collection_urls(self) -> list[str]:
        return [
            f"https://x.com/{account.lstrip('@')}" for account in self.config.get_list("accounts")
        ]

    def permalink_for(self, entry: dict[str, Any]) -> str:
        url = entry.get("url") or entry.get("webpage_url")
        if url:
            return str(url)
        uploader = entry.get("uploader") or "i"
        return f"https://x.com/{uploader}/status/{entry.get('id')}"

    async def status(self) -> SourceStatus:
        base = await super().status()
        if base.configured:
            return SourceStatus(
                name=base.name,
                enabled=base.enabled,
                configured=True,
                detail=f"backend scrape, {base.detail}",
            )
        return base


def build_x_source(*args: Any, **kwargs: Any) -> SourceAdapter:
    """Devuelve el backend de X que corresponda segun `SCRAPPY_X_BACKEND`."""
    settings = args[0] if args else kwargs["settings"]
    if settings.x_backend is XBackend.SCRAPE:
        return XScrapeSource(*args, **kwargs)
    return XApiSource(*args, **kwargs)


def _parse_iso(value: Any) -> datetime:
    """Fecha ISO-8601 de la API de X, tolerando el sufijo `Z`."""
    if not isinstance(value, str):
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return utcnow()
