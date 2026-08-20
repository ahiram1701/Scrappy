"""Base comun para las fuentes que se descubren con yt-dlp.

X (en modo `scrape`), TikTok e Instagram no ofrecen API publica utilizable para
este caso de uso, asi que las tres se descubren enumerando colecciones con
yt-dlp. El patron es identico en las tres —construir URLs de perfil o hashtag,
enumerarlas, normalizar las entradas— y eso vive aqui una sola vez.

Las tres marcan `requires_tos_ack = True`: usarlas incumple los terminos de sus
plataformas y solo se activan si el usuario lo autoriza explicitamente. Ver
`docs/LEGAL.md`.
"""

from __future__ import annotations

import abc
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scrappy.core.errors import DownloadError, RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.download.ytdlp_engine import YtDlpEngine
from scrappy.sources.base import SourceAdapter, SourceStatus, rotar_objetivos

# Lo que dicen los extractores de yt-dlp cuando la plataforma nos esta frenando.
# No hay excepcion tipada para esto: el mensaje es todo lo que llega.
_MARCAS_DE_RATE_LIMIT = (
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "try again later",
    "temporarily locked",
)

# Espaciado por defecto entre colecciones. Ninguna de estas plataformas publica
# cual es su limite, asi que el numero no sale de una tabla: sale de quedarse
# por debajo del ritmo al que una persona abriria tres perfiles seguidos.
_DEFAULT_DELAY_SECONDS = 15.0


def _es_rate_limit(mensaje: str) -> bool:
    """Si el texto de un error de yt-dlp huele a limite de peticiones."""
    bajo = mensaje.casefold()
    return any(marca in bajo for marca in _MARCAS_DE_RATE_LIMIT)


class YtDlpSource(SourceAdapter, abc.ABC):
    """Fuente que enumera colecciones con yt-dlp y normaliza sus entradas."""

    requires_tos_ack = True

    #: Nombre legible de la plataforma, para los mensajes de estado.
    platform_label: str = ""
    #: Si la plataforma es inutilizable sin cookies de una sesion iniciada.
    cookies_required: bool = False
    #: Espera por defecto entre colecciones, si el YAML no dice otra cosa.
    default_delay_seconds: float = _DEFAULT_DELAY_SECONDS
    #: Espera que yt-dlp mete entre peticiones dentro de una misma coleccion.
    sleep_requests: float = 0.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._engine = YtDlpEngine(max_bytes=self.settings.max_download_bytes)

    # ------------------------------------------------------------------
    # A definir por cada plataforma
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def collection_urls(self) -> list[str]:
        """URLs de perfiles, hashtags o busquedas a enumerar."""

    @abc.abstractmethod
    def permalink_for(self, entry: dict[str, Any]) -> str:
        """URL publica del post original, imprescindible para la atribucion."""

    @property
    def cookies_file(self) -> Path | None:
        return self.settings.cookies_file_for(self.name)

    def minimum_engagement(self) -> int:
        """Umbral duro de engagement por debajo del cual el item ni se considera."""
        return 0

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    async def status(self) -> SourceStatus:
        enabled = getattr(self.settings, f"{self.name}_enabled", False)

        if not self.settings.enable_tos_risky_sources:
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail="bloqueada: requiere SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true "
                "(lee docs/LEGAL.md)",
            )

        cookies = self.cookies_file
        if self.cookies_required and (cookies is None or not cookies.exists()):
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail=f"{self.platform_label} necesita un fichero de cookies valido",
            )
        if cookies is not None and not cookies.exists():
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail=f"el fichero de cookies {cookies} no existe",
            )

        targets = self.collection_urls()
        if not targets:
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail="no hay hashtags ni cuentas en config/sources.yaml",
            )
        por_ronda = self.config.get_int("objetivos_por_ronda", len(targets))
        detalle = f"{len(targets)} colecciones"
        if por_ronda < len(targets):
            # Que se consulten por tramos no es un detalle menor: explica por
            # que una ronda trae menos candidatos de los que uno esperaria.
            detalle += f", {max(por_ronda, 1)} por ronda"
        return SourceStatus(
            name=self.name,
            enabled=enabled,
            configured=True,
            detail=detalle,
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        self.ensure_tos_acknowledged()

        cookies = self.cookies_file
        if self.cookies_required and (cookies is None or not cookies.exists()):
            # Sin sesion no hay nada que enumerar, y salir de aqui es mejor que
            # gastar una peticion en confirmarlo. `status()` ya lo cuenta en
            # `/sources`; esto solo evita intentarlo cada cuatro horas.
            self.log.warning("sin_cookies", detail=f"{self.platform_label} necesita cookies")
            return []

        todos = self.collection_urls()
        if not todos:
            return []

        # Estas plataformas no toleran que se les pida la lista entera de una
        # tacada: se rota por tramos y se espacia, igual que hace Reddit con sus
        # subreddits. Consultar pocas colecciones por ronda es lo que evita que
        # te marquen; el resto se cubre en las rondas siguientes.
        targets = rotar_objetivos(todos, self.config.get_int("objetivos_por_ronda", len(todos)))
        delay = float(self.config.get_int("delay_seconds", int(self.default_delay_seconds)))

        per_target = max(budget // len(targets), 5)
        minimum = self.minimum_engagement()

        candidates: list[RawCandidate] = []
        for index, url in enumerate(targets):
            if index:
                await asyncio.sleep(delay)
            try:
                entries = await self._enumerar(url, per_target)
            except RateLimitedError:
                # Si ya nos estan limitando, seguir con el resto de la lista
                # solo confirma el patron que nos ha delatado.
                self.log.warning("rate_limited_stop", url=url)
                break
            except DownloadError as exc:
                # Un hashtag vacio o un perfil privado no debe tumbar la fuente.
                self.log.warning("collection_failed", url=url, error=str(exc))
                continue
            except Exception as exc:  # yt-dlp lanza excepciones de muchos tipos
                raise SourceError(self.name, f"fallo inesperado con {url}: {exc}") from exc

            for entry in entries:
                candidate = self.to_candidate(entry)
                if candidate is None or candidate.engagement < minimum:
                    continue
                candidates.append(candidate)

        self.log.info("discovered", count=len(candidates), collections=len(targets), de=len(todos))
        return candidates

    async def _enumerar(self, url: str, limit: int) -> list[dict[str, Any]]:
        """Enumera una coleccion, distinguiendo un rate limit de un fallo normal.

        yt-dlp no tiene excepciones tipadas para esto: todo llega como
        `DownloadError` con el mensaje del extractor dentro. Mirar el texto es
        feo, pero es la unica forma de saber si conviene parar o seguir, y la
        diferencia entre las dos cosas es justo la que importa.
        """
        try:
            return await self._engine.enumerate(
                url,
                limit=limit,
                cookies_file=self.cookies_file,
                sleep_requests=self.sleep_requests,
            )
        except DownloadError as exc:
            if _es_rate_limit(str(exc)):
                raise RateLimitedError(self.name) from exc
            raise

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def to_candidate(self, entry: dict[str, Any]) -> RawCandidate | None:
        """Convierte una entrada de yt-dlp en `RawCandidate`.

        Las entradas de `extract_flat` traen pocos campos y varian mucho entre
        extractores, asi que todo se lee de forma defensiva.
        """
        entry_id = entry.get("id")
        if not entry_id:
            return None

        engagement = _first_int(entry, ("like_count", "view_count", "play_count", "repost_count"))
        comments = _first_int(entry, ("comment_count",))

        return RawCandidate(
            source=self.name,
            source_id=str(entry_id),
            permalink=self.permalink_for(entry),
            title=str(entry.get("title") or entry.get("description") or "")[:500],
            author=str(entry.get("uploader") or entry.get("channel") or "desconocido"),
            author_url=entry.get("uploader_url") or entry.get("channel_url"),
            kind=MediaKind.VIDEO,
            media_url=None,  # siempre via yt-dlp
            thumbnail_url=entry.get("thumbnail"),
            duration_seconds=_as_float(entry.get("duration")),
            created_at=_timestamp_of(entry),
            engagement=engagement,
            comments=comments,
            nsfw=bool(entry.get("age_limit") or 0),
            language=entry.get("language"),
            extra={"extractor": entry.get("ie_key") or entry.get("extractor")},
        )


# ---------------------------------------------------------------------------
# Helpers de lectura defensiva
# ---------------------------------------------------------------------------
def _first_int(entry: dict[str, Any], keys: tuple[str, ...]) -> int:
    """Primer valor entero no nulo entre varias claves alternativas."""
    for key in keys:
        value = entry.get(key)
        if value is None:
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return 0


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp_of(entry: dict[str, Any]) -> datetime:
    """Fecha de publicacion, probando los formatos que usa yt-dlp.

    Si la entrada no la trae —muy comun en `extract_flat`— se asume "ahora".
    Es una aproximacion conservadora: el termino de velocidad del ranking
    quedara alto, y el filtro de antiguedad no descartara el item por error.
    """
    timestamp = entry.get("timestamp")
    if timestamp is not None:
        try:
            return datetime.fromtimestamp(float(timestamp), tz=UTC)
        except (TypeError, ValueError, OSError):
            pass

    upload_date = entry.get("upload_date")
    if isinstance(upload_date, str) and len(upload_date) == 8:
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            pass

    return utcnow()
