"""Adapter de Reddit sobre la API oficial.

Es la fuente recomendada y la unica que no plantea problemas de terminos de
servicio: Reddit publica una API documentada, con OAuth, y aqui se usa tal cual.

Se autentica con el flujo *application-only* (`client_credentials`), que da
acceso de solo lectura a lo publico sin necesitar la contrasena de ninguna
cuenta. Basta con crear una app de tipo `script` en
https://www.reddit.com/prefs/apps y copiar el id y el secreto al `.env`.

No se usa PRAW a proposito: solo hacen falta dos endpoints y asi todos los
adapters comparten el mismo cliente httpx con reintentos (ver
`docs/adr/0002-httpx-en-vez-de-praw.md`).
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"

# Se renueva el token con margen para no perder una peticion por caducidad.
_TOKEN_MARGIN_SECONDS = 60

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_ANIMATION_SUFFIXES = (".gif",)

# Dominios que yt-dlp resuelve bien y que suelen traer video de calidad.
_VIDEO_DOMAINS = frozenset(
    {
        "v.redd.it",
        "redgifs.com",
        "www.redgifs.com",
        "streamable.com",
        "gfycat.com",
        "imgur.com",
        "i.imgur.com",
        "youtube.com",
        "youtu.be",
    }
)


class RedditSource(SourceAdapter):
    """Descubre posts en los subreddits configurados en `sources.yaml`."""

    name = "reddit"
    requires_tos_ack = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    async def status(self) -> SourceStatus:
        has_creds = bool(
            self.settings.reddit_client_id.get_secret_value()
            and self.settings.reddit_client_secret.get_secret_value()
        )
        subreddits = self.config.get_list("subreddits")
        if not has_creds:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.reddit_enabled,
                configured=False,
                detail="faltan SCRAPPY_REDDIT_CLIENT_ID / SCRAPPY_REDDIT_CLIENT_SECRET",
            )
        if not subreddits:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.reddit_enabled,
                configured=False,
                detail="no hay subreddits en config/sources.yaml",
            )
        return SourceStatus(
            name=self.name,
            enabled=self.settings.reddit_enabled,
            configured=True,
            detail=f"{len(subreddits)} subreddits",
        )

    # ------------------------------------------------------------------
    # Autenticacion
    # ------------------------------------------------------------------
    async def _access_token(self) -> str:
        """Token de aplicacion, cacheado hasta poco antes de su caducidad."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        client_id = self.settings.reddit_client_id.get_secret_value()
        client_secret = self.settings.reddit_client_secret.get_secret_value()
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

        try:
            response = await self.client.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {basic}",
                    "User-Agent": self.settings.reddit_user_agent,
                },
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con Reddit: {exc}") from exc

        if response.status_code == 401:
            raise SourceError(
                self.name,
                "credenciales rechazadas. Revisa que la app sea de tipo 'script' y "
                "que el id y el secreto sean los correctos.",
            )
        if response.status_code != 200:
            raise SourceError(
                self.name, f"el endpoint de token devolvio HTTP {response.status_code}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise SourceError(self.name, "la respuesta de token no incluye access_token")

        self._token = str(token)
        expires_in = float(payload.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + max(expires_in - _TOKEN_MARGIN_SECONDS, 30)
        return self._token

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        subreddits = self.config.get_list("subreddits")
        if not subreddits:
            return []

        listing = self.config.get_str("listing", "top")
        time_filter = self.config.get_str("time_filter", "day")
        min_score = self.config.get_int("min_score", 0)

        # Se autentica ANTES del bucle a proposito. Si las credenciales son
        # invalidas, el error debe tumbar la fuente entera de inmediato en vez
        # de reintentarse una vez por subreddit: seria una ristra de peticiones
        # condenadas al fallo, y un camino rapido al rate limit.
        await self._access_token()

        # El presupuesto se reparte entre subreddits, con un minimo razonable
        # para que anadir muchos subreddits no deje a cada uno con 2 posts.
        per_sub = max(budget // max(len(subreddits), 1), 10)

        candidates: list[RawCandidate] = []
        for subreddit in subreddits:
            try:
                posts = await self._fetch_listing(subreddit, listing, time_filter, per_sub)
            except RateLimitedError:
                raise
            except SourceError as exc:
                # Un subreddit privado o inexistente no debe tumbar la fuente.
                self.log.warning("subreddit_failed", subreddit=subreddit, error=str(exc))
                continue

            for post in posts:
                candidate = self._to_candidate(post, subreddit)
                if candidate is None:
                    continue
                if candidate.engagement < min_score:
                    continue
                candidates.append(candidate)

        self.log.info("discovered", count=len(candidates), subreddits=len(subreddits))
        return candidates

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _fetch_listing(
        self, subreddit: str, listing: str, time_filter: str, limit: int
    ) -> list[dict[str, Any]]:
        token = await self._access_token()
        params: dict[str, Any] = {"limit": min(limit, 100), "raw_json": 1}
        if listing == "top":
            params["t"] = time_filter

        try:
            response = await self.client.get(
                f"{_API_BASE}/r/{subreddit}/{listing}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": self.settings.reddit_user_agent,
                },
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"r/{subreddit}: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise RateLimitedError(self.name, float(retry_after) if retry_after else None)
        if response.status_code == 403:
            raise SourceError(self.name, f"r/{subreddit} es privado o esta restringido")
        if response.status_code == 404:
            raise SourceError(self.name, f"r/{subreddit} no existe")
        if response.status_code != 200:
            raise SourceError(self.name, f"r/{subreddit}: HTTP {response.status_code}")

        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        return [child.get("data", {}) for child in children if isinstance(child, dict)]

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidate(self, post: dict[str, Any], subreddit: str) -> RawCandidate | None:
        """Convierte un post de Reddit en `RawCandidate`, o None si no sirve.

        Se descartan los posts de solo texto, las encuestas y los enlaces a
        dominios de los que no sabemos extraer medio.
        """
        if post.get("stickied") or post.get("is_self") or post.get("pinned"):
            return None
        if post.get("removed_by_category"):
            return None

        post_id = post.get("id")
        if not post_id:
            return None

        url = str(post.get("url_overridden_by_dest") or post.get("url") or "")
        kind, media_url = self._classify(post, url)
        if kind is None:
            return None

        permalink = f"https://www.reddit.com{post.get('permalink', '')}"
        author = str(post.get("author") or "desconocido")

        duration: float | None = None
        reddit_video = (post.get("media") or {}).get("reddit_video") or {}
        if reddit_video.get("duration"):
            duration = float(reddit_video["duration"])

        thumbnail = post.get("thumbnail")
        if thumbnail in {"self", "default", "nsfw", "spoiler", "image", ""}:
            thumbnail = None

        return RawCandidate(
            source=self.name,
            source_id=str(post_id),
            permalink=permalink,
            title=str(post.get("title") or ""),
            author=author,
            author_url=f"https://www.reddit.com/user/{author}",
            kind=kind,
            media_url=media_url,
            thumbnail_url=thumbnail,
            duration_seconds=duration,
            created_at=self._created_at(post),
            engagement=int(post.get("score") or 0),
            comments=int(post.get("num_comments") or 0),
            nsfw=bool(post.get("over_18")),
            language=None,
            extra={"subreddit": subreddit, "upvote_ratio": post.get("upvote_ratio")},
        )

    @staticmethod
    def _created_at(post: dict[str, Any]) -> Any:
        from datetime import UTC, datetime

        epoch = post.get("created_utc")
        if epoch is None:
            return utcnow()
        return datetime.fromtimestamp(float(epoch), tz=UTC)

    @staticmethod
    def _classify(post: dict[str, Any], url: str) -> tuple[MediaKind | None, str | None]:
        """Decide el tipo de medio y si hay URL directa o hace falta yt-dlp.

        Devuelve `(None, None)` cuando el post no contiene medio aprovechable.
        """
        lowered = url.lower().split("?")[0]

        # Video alojado en Reddit: el audio va en una pista DASH separada, asi
        # que se delega en yt-dlp en vez de usar la URL directa (que seria muda).
        if post.get("is_video") or "v.redd.it" in lowered:
            return MediaKind.VIDEO, None

        if lowered.endswith(_ANIMATION_SUFFIXES):
            return MediaKind.ANIMATION, url
        if lowered.endswith(".gifv") or lowered.endswith(".mp4"):
            return MediaKind.ANIMATION, None
        if lowered.endswith(_IMAGE_SUFFIXES):
            return MediaKind.PHOTO, url

        hint = post.get("post_hint")
        if hint == "image":
            return MediaKind.PHOTO, url
        if hint in {"hosted:video", "rich:video"}:
            return MediaKind.VIDEO, None

        domain = str(post.get("domain") or "").lower()
        if domain in _VIDEO_DOMAINS:
            return MediaKind.VIDEO, None

        # Galerias, enlaces a articulos, crossposts sin medio: no interesan.
        return None, None
