"""Envoltura de yt-dlp.

Concentra en un solo sitio todas las opciones de yt-dlp: seleccion de formato,
limites de tamano, cookies y silenciado de la salida. Los adapters y el
descargador solo llaman a metodos de aqui, de modo que ajustar el
comportamiento de yt-dlp para todo el proyecto es tocar un fichero.

yt-dlp es sincrono y bloqueante, asi que cada llamada se manda a un hilo con
`asyncio.to_thread` para no congelar el bucle de eventos del bot.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from scrappy.core.errors import DependencyMissingError, DownloadError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# Se pide 720p como maximo: por encima de eso el fichero crece mucho, Telegram
# lo reescala igualmente en la mayoria de clientes, y el limite de subida del
# bot son 50 MB. Se prioriza mp4/m4a para que no haga falta remuxear.
_FORMAT_SELECTOR = (
    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height<=720][ext=mp4]/"
    "best[height<=720]/"
    "best"
)


def ffmpeg_available() -> bool:
    """True si hay un ffmpeg utilizable en el PATH."""
    return shutil.which("ffmpeg") is not None


def require_ffmpeg() -> None:
    """Falla con un mensaje util si falta ffmpeg.

    Raises:
        DependencyMissingError: no hay ffmpeg en el PATH.
    """
    if not ffmpeg_available():
        raise DependencyMissingError(
            "ffmpeg no esta en el PATH y es imprescindible para unir video y audio "
            "y para normalizar los clips.\n"
            "  Windows : winget install Gyan.FFmpeg\n"
            "  macOS   : brew install ffmpeg\n"
            "  Debian  : sudo apt install ffmpeg\n"
            "La imagen Docker ya lo incluye."
        )


class YtDlpEngine:
    """Acceso a yt-dlp para enumerar y descargar."""

    def __init__(self, *, max_bytes: int | None = None) -> None:
        self._max_bytes = max_bytes

    # ------------------------------------------------------------------
    # Opciones
    # ------------------------------------------------------------------
    def _base_options(self, cookies_file: Path | None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "ignoreerrors": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            # Sin cache en disco: coherente con no dejar rastro del contenido.
            "cachedir": False,
        }
        if cookies_file is not None:
            if not cookies_file.exists():
                raise DownloadError(f"el fichero de cookies {cookies_file} no existe")
            options["cookiefile"] = str(cookies_file)
        return options

    # ------------------------------------------------------------------
    # Enumeracion (solo metadatos, no descarga nada)
    # ------------------------------------------------------------------
    async def enumerate(
        self, url: str, *, limit: int, cookies_file: Path | None = None
    ) -> list[dict[str, Any]]:
        """Lista las entradas de una URL de coleccion (perfil, hashtag, busqueda).

        Usa `extract_flat`, que solo lee el indice y no visita cada video, de
        modo que enumerar 40 candidatos cuesta una peticion y no cuarenta.
        """
        return await asyncio.to_thread(self._enumerate_sync, url, limit, cookies_file)

    def _enumerate_sync(
        self, url: str, limit: int, cookies_file: Path | None
    ) -> list[dict[str, Any]]:
        options = self._base_options(cookies_file)
        options.update(
            {
                "extract_flat": "in_playlist",
                "playlistend": limit,
                "noplaylist": False,
            }
        )
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except YtDlpDownloadError as exc:
            raise DownloadError(f"yt-dlp no pudo enumerar {url}: {exc}") from exc

        if not info:
            return []
        entries = info.get("entries") or []
        return [entry for entry in entries if isinstance(entry, dict)]

    async def probe(self, url: str, *, cookies_file: Path | None = None) -> dict[str, Any] | None:
        """Metadatos completos de un unico medio, sin descargarlo."""
        return await asyncio.to_thread(self._probe_sync, url, cookies_file)

    def _probe_sync(self, url: str, cookies_file: Path | None) -> dict[str, Any] | None:
        options = self._base_options(cookies_file)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except YtDlpDownloadError as exc:
            log.debug("probe_failed", url=url, error=str(exc))
            return None
        return info if isinstance(info, dict) else None

    # ------------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------------
    async def download(
        self, url: str, destination_dir: Path, *, cookies_file: Path | None = None
    ) -> tuple[Path, dict[str, Any]]:
        """Descarga el medio dentro de `destination_dir`.

        `destination_dir` es siempre un `EphemeralWorkspace`, asi que el fichero
        que se devuelve tiene los ciclos contados.

        Returns:
            La ruta del fichero descargado y los metadatos de yt-dlp.

        Raises:
            DownloadError: yt-dlp fallo o no produjo ningun fichero.
        """
        return await asyncio.to_thread(self._download_sync, url, destination_dir, cookies_file)

    def _download_sync(
        self, url: str, destination_dir: Path, cookies_file: Path | None
    ) -> tuple[Path, dict[str, Any]]:
        options = self._base_options(cookies_file)
        options.update(
            {
                "format": _FORMAT_SELECTOR,
                "merge_output_format": "mp4",
                "outtmpl": str(destination_dir / "media.%(ext)s"),
                "paths": {"home": str(destination_dir), "temp": str(destination_dir)},
                # Sin ignoreerrors: aqui un fallo si es un fallo del item.
                "ignoreerrors": False,
                "writethumbnail": False,
                "writeinfojson": False,
                "writesubtitles": False,
            }
        )
        if self._max_bytes is not None:
            # Corta la descarga en cuanto yt-dlp sabe que el fichero se pasa,
            # antes de gastar ancho de banda y espacio.
            options["max_filesize"] = self._max_bytes

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except YtDlpDownloadError as exc:
            raise DownloadError(f"yt-dlp fallo con {url}: {exc}") from exc

        if not isinstance(info, dict):
            raise DownloadError(f"yt-dlp no devolvio metadatos para {url}")

        produced = [
            item
            for item in sorted(destination_dir.iterdir())
            if item.is_file() and not item.name.endswith(".part")
        ]
        if not produced:
            raise DownloadError(
                f"yt-dlp no genero ningun fichero para {url} "
                "(probablemente supera max_filesize o el medio es privado)"
            )

        # El fichero util es el mayor: los restos de fragmentos son pequenos.
        media_path = max(produced, key=lambda item: item.stat().st_size)
        return media_path, info
