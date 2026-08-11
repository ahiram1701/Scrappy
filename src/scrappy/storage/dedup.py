"""Deduplicacion en tres puertas.

Un mismo meme llega por varias fuentes, recomprimido y recortado. Compararlo
solo por URL no sirve de nada. Se aplican tres comprobaciones en orden de coste
creciente, para descartar lo antes posible y no gastar ancho de banda ni disco:

    1. uid      `source:source_id`. Cuesta una consulta. Se hace ANTES de descargar.
    2. sha256   identidad byte a byte. Se calcula en streaming mientras se descarga.
    3. phash    similitud perceptual. Detecta el mismo video recomprimido en otra
                plataforma, que es el caso real que mas ensucia un canal.

La tercera es la unica cara, y aun asi se limita a los hashes de los ultimos 90
dias (ver `known_phashes`).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import subprocess
from pathlib import Path

import imagehash
from PIL import Image

from scrappy.core.errors import DuplicateItemError
from scrappy.core.models import EphemeralMedia, MediaKind
from scrappy.observability.logging import get_logger
from scrappy.storage.backends import StateBackendProtocol

log = get_logger(__name__)

# Instantes del video, en fraccion de su duracion, de los que se extrae un frame.
# Se evitan el 0.0 y el 1.0 porque muchos clips empiezan o acaban en negro y eso
# haria que videos distintos compartieran hash.
_FRAME_POSITIONS = (0.25, 0.50, 0.75)

_HASH_CHUNK = 1024 * 1024


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path) -> str:
    """SHA-256 de un fichero, leyendolo por trozos para no cargarlo entero."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def phash_of_image_bytes(data: bytes) -> str | None:
    """pHash perceptual de una imagen en memoria."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return str(imagehash.phash(image.convert("RGB")))
    except Exception as exc:  # Pillow lanza de todo ante ficheros corruptos
        log.debug("phash_image_failed", error=str(exc))
        return None


def _extract_frame(path: Path, position_seconds: float) -> bytes | None:
    """Saca un frame PNG del video con ffmpeg, sin escribir nada en disco.

    La salida va por stdout (`-f image2pipe`), de modo que el frame nunca se
    materializa como fichero. Coherente con la garantia de contenido efimero.
    """
    command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-ss",
        f"{position_seconds:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=256:-1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        # Comando fijo, sin shell: la unica parte variable es una ruta que
        # generamos nosotros dentro del workspace efimero.
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("frame_extract_failed", error=str(exc))
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _phash_of_video_sync(path: Path, duration: float | None) -> str | None:
    """Hash perceptual de un video: concatena el pHash de tres frames.

    Se concatenan en vez de promediarse para que la distancia de Hamming del
    conjunto siga siendo interpretable: 3 x 64 bits, y el umbral se compara
    contra la suma. Un clip que coincide en dos de tres frames sigue siendo el
    mismo clip con otro corte.
    """
    if duration is None or duration <= 0:
        positions = [1.0]
    else:
        positions = [duration * fraction for fraction in _FRAME_POSITIONS]

    parts: list[str] = []
    for position in positions:
        frame = _extract_frame(path, position)
        if frame is None:
            continue
        digest = phash_of_image_bytes(frame)
        if digest is not None:
            parts.append(digest)

    if not parts:
        return None
    return "".join(parts)


async def phash_of_video(path: Path, duration: float | None) -> str | None:
    """Version asincrona: ffmpeg es bloqueante, se manda a un hilo."""
    return await asyncio.to_thread(_phash_of_video_sync, path, duration)


def hamming_distance(left: str, right: str) -> int:
    """Distancia de Hamming entre dos hashes hexadecimales.

    Si tienen longitudes distintas (por ejemplo un video con 3 frames frente a
    otro con 1) se devuelve un valor imposible de superar el umbral, porque no
    son comparables.
    """
    if len(left) != len(right):
        return 10_000
    try:
        return int(imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right))
    except ValueError:
        # Hashes concatenados: se comparan por bloques de 16 hex (64 bits).
        block = 16
        if len(left) % block != 0:
            return 10_000
        return sum(
            int(
                imagehash.hex_to_hash(left[i : i + block])
                - imagehash.hex_to_hash(right[i : i + block])
            )
            for i in range(0, len(left), block)
        )


class Deduplicator:
    """Aplica las tres puertas contra el backend de estado activo."""

    def __init__(self, state: StateBackendProtocol, *, phash_threshold: int = 6) -> None:
        self._state = state
        self._threshold = phash_threshold

    async def is_known_uid(self, uid: str) -> bool:
        """Puerta 1. Se llama antes de descargar: evita trafico y disco."""
        return await self._state.has_uid(uid)

    async def compute_hashes(self, media: EphemeralMedia) -> EphemeralMedia:
        """Rellena `sha256` y `phash` del medio.

        Se ejecuta obligatoriamente ANTES de que el workspace se borre: despues
        ya no habria de donde sacarlos.
        """
        if media.in_memory:
            assert media.data is not None
            media.sha256 = sha256_of_bytes(media.data)
            media.phash = phash_of_image_bytes(media.data)
        else:
            assert media.path is not None
            media.sha256 = await asyncio.to_thread(sha256_of_file, media.path)
            if media.kind.is_video_like:
                media.phash = await phash_of_video(media.path, media.duration_seconds)
            else:
                media.phash = phash_of_image_bytes(media.path.read_bytes())
        return media

    async def assert_not_duplicate(self, media: EphemeralMedia) -> None:
        """Puertas 2 y 3.

        Raises:
            DuplicateItemError: el medio ya se publico, exacta o perceptualmente.
        """
        if media.sha256 and await self._state.has_sha256(media.sha256):
            raise DuplicateItemError("sha256 identico")

        if media.phash is None:
            return

        for uid, known in await self._state.known_phashes():
            distance = hamming_distance(media.phash, known)
            if distance <= self._threshold:
                raise DuplicateItemError(f"phash a distancia {distance} de {uid}")

    @staticmethod
    def describe_thresholds(threshold: int, kind: MediaKind) -> str:
        """Texto de ayuda para `/config` y para la documentacion."""
        return (
            f"{kind}: se considera duplicado si la distancia de Hamming del pHash es <= {threshold}"
        )
