"""Orquestador de descarga: decide por donde va cada medio.

Reglas, en una frase: si es imagen o GIF con URL directa va a memoria; si es
video o hay que resolver la URL, va a yt-dlp dentro de un workspace efimero.

El `EphemeralWorkspace` lo abre y lo cierra quien llama (el pipeline), no este
modulo. Esa separacion es a proposito: quien crea el recurso es quien lo
destruye, y asi no hay forma de que una ruta de error se salte el borrado.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from scrappy.config.settings import Settings
from scrappy.core.errors import DownloadError, MediaTooLargeError
from scrappy.core.models import EphemeralMedia, MediaKind, RawCandidate
from scrappy.download.http_engine import HttpEngine
from scrappy.download.transcode import inspect, shrink_to_limit
from scrappy.download.ytdlp_engine import YtDlpEngine
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# Extensiones que Telegram trata bien como foto directa.
_PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class Downloader:
    """Obtiene el medio de un candidato, siempre de forma efimera."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = HttpEngine(client, max_bytes=settings.max_download_bytes)
        self._ytdlp = YtDlpEngine(max_bytes=settings.max_download_bytes)

    async def fetch(self, candidate: RawCandidate, workspace: Path) -> EphemeralMedia:
        """Descarga el medio del candidato.

        Args:
            candidate: el item a descargar.
            workspace: directorio efimero donde puede escribir. Quien lo creo
                lo borrara pase lo que pase.

        Returns:
            El medio, en memoria o apuntando a un fichero dentro de `workspace`.

        Raises:
            DownloadError: no se pudo obtener el medio.
            MediaTooLargeError: no cabe en el limite ni reencodado.
        """
        bound = log.bind(uid=candidate.uid, kind=str(candidate.kind))

        if candidate.media_url and not candidate.kind.is_video_like:
            bound.debug("download_via_http")
            return await self._fetch_to_memory(candidate)

        # Los GIF de verdad (no los .mp4 disfrazados) son pequenos y Telegram
        # los acepta tal cual: memoria y listo.
        if (
            candidate.media_url
            and candidate.kind is MediaKind.ANIMATION
            and candidate.media_url.lower().split("?")[0].endswith(".gif")
        ):
            bound.debug("download_gif_via_http")
            return await self._fetch_to_memory(candidate)

        bound.debug("download_via_ytdlp")
        return await self._fetch_to_workspace(candidate, workspace)

    # ------------------------------------------------------------------
    # Camino en memoria
    # ------------------------------------------------------------------
    async def _fetch_to_memory(self, candidate: RawCandidate) -> EphemeralMedia:
        assert candidate.media_url is not None
        data, content_type = await self._http.fetch(candidate.media_url)

        suffix = _suffix_for(candidate.media_url, content_type)
        kind = candidate.kind
        if kind is MediaKind.PHOTO and suffix not in _PHOTO_SUFFIXES:
            # Un `.gif` anunciado como foto se envia como animacion o Telegram
            # lo mostrara congelado.
            kind = MediaKind.ANIMATION

        return EphemeralMedia(
            candidate=candidate,
            kind=kind,
            filename=f"{candidate.source}-{candidate.source_id}{suffix}",
            data=data,
            size_bytes=len(data),
            duration_seconds=candidate.duration_seconds,
        )

    # ------------------------------------------------------------------
    # Camino en workspace
    # ------------------------------------------------------------------
    async def _fetch_to_workspace(self, candidate: RawCandidate, workspace: Path) -> EphemeralMedia:
        target_url = candidate.media_url or candidate.permalink
        cookies = self._settings.cookies_file_for(candidate.source)

        path, info = await self._ytdlp.download(target_url, workspace, cookies_file=cookies)

        size = path.stat().st_size
        limit = self._settings.upload_limit_bytes
        if size > limit:
            # No se rinde a la primera: casi todo cabe reencodando a 720p.
            path = await shrink_to_limit(path, limit)
            size = path.stat().st_size

        if size > limit:  # pragma: no cover - shrink_to_limit ya lanzaria
            raise MediaTooLargeError(size, limit)

        probe = await inspect(path)
        kind = candidate.kind
        if kind is MediaKind.VIDEO and not probe.has_audio:
            # Un video mudo se ve mucho mejor como animacion: Telegram lo
            # reproduce en bucle y sin controles de sonido inutiles.
            kind = MediaKind.ANIMATION

        if not path.exists():  # pragma: no cover - defensivo
            raise DownloadError("el fichero descargado desaparecio antes de publicarse")

        return EphemeralMedia(
            candidate=candidate,
            kind=kind,
            filename=path.name,
            path=path,
            size_bytes=size,
            width=probe.width,
            height=probe.height,
            duration_seconds=probe.duration_seconds
            or _as_float(info.get("duration"))
            or candidate.duration_seconds,
        )


def _suffix_for(url: str, content_type: str) -> str:
    """Extension a usar, deducida de la URL y, si no, del content-type."""
    clean = url.lower().split("?")[0]
    for suffix in (*_PHOTO_SUFFIXES, ".gif", ".mp4"):
        if clean.endswith(suffix):
            return suffix

    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
    }.get(content_type, ".bin")


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
