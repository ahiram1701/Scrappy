"""Descarga directa por HTTP, enteramente en memoria.

Se usa para imagenes y GIFs, que es donde se puede evitar el disco por
completo: los bytes llegan a un `bytearray`, se envian a Telegram y se pierden
cuando el objeto sale de alcance. Ni un fichero temporal.

Los videos no pasan por aqui: yt-dlp y ffmpeg necesitan poder hacer seeking
sobre un fichero, asi que van al workspace efimero.
"""

from __future__ import annotations

import httpx

from scrappy.core.errors import DownloadError, MediaTooLargeError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

_CHUNK = 64 * 1024

# Tipos que Telegram acepta como foto o animacion sin conversion previa.
_ACCEPTED_PREFIXES = ("image/", "video/mp4", "application/octet-stream")


class HttpEngine:
    """Descarga un fichero a memoria con un limite de tamano estricto."""

    def __init__(self, client: httpx.AsyncClient, *, max_bytes: int) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """Descarga `url` y devuelve `(bytes, content_type)`.

        El limite se comprueba dos veces: primero con la cabecera
        `Content-Length` (para abortar antes de descargar nada) y despues
        acumulando trozos (porque muchos servidores no la envian).

        Raises:
            MediaTooLargeError: el fichero supera `max_bytes`.
            DownloadError: la respuesta no es utilizable.
        """
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise DownloadError(f"HTTP {response.status_code} al descargar {url}")

                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type and not content_type.startswith(_ACCEPTED_PREFIXES):
                    raise DownloadError(
                        f"el servidor devolvio {content_type} en vez de un medio. "
                        "Suele significar que el enlace lleva a una pagina y no "
                        "al fichero, o que la plataforma respondio con un error "
                        "en HTML. Este item se salta."
                    )

                declared = response.headers.get("content-length")
                if declared and int(declared) > self._max_bytes:
                    raise MediaTooLargeError(int(declared), self._max_bytes)

                buffer = bytearray()
                async for chunk in response.aiter_bytes(_CHUNK):
                    buffer.extend(chunk)
                    if len(buffer) > self._max_bytes:
                        # Se corta la conexion en cuanto se pasa: no tiene
                        # sentido terminar de bajar algo que se va a tirar.
                        raise MediaTooLargeError(len(buffer), self._max_bytes)

        except httpx.HTTPError as exc:
            raise DownloadError(f"fallo de red al descargar {url}: {exc}") from exc

        if not buffer:
            raise DownloadError(f"{url} devolvio un cuerpo vacio")

        log.debug("http_fetched", url=url, size=len(buffer), content_type=content_type)
        return bytes(buffer), content_type
