"""Normalizacion de video con ffmpeg.

Telegram acepta como maximo 50 MB por subida desde un bot, y ademas reproduce
mucho mejor los clips en H.264/AAC dentro de un MP4 con el atomo `moov` al
principio (`faststart`), que es lo que permite empezar a reproducir sin
descargar el fichero entero.

Este modulo hace dos cosas:

  1. `inspect`   lee dimensiones y duracion reales con ffprobe.
  2. `shrink_to_limit`  reencoda un clip que se pasa de tamano, calculando el
     bitrate a partir del tamano objetivo y la duracion.

El fichero de entrada se borra en cuanto termina el reencode: en ningun momento
hay dos copias del mismo video vivas mas de lo imprescindible.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scrappy.core.errors import TranscodeError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# Se reserva un 8% para la sobrecarga del contenedor y el redondeo del encoder.
_SIZE_SAFETY_MARGIN = 0.92
_AUDIO_BITRATE_BPS = 96_000
_MIN_VIDEO_BITRATE_BPS = 150_000
_FFMPEG_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """Lo que ffprobe sabe de un fichero."""

    width: int | None
    height: int | None
    duration_seconds: float | None
    has_audio: bool


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        # El comando se construye aqui con argumentos fijos y sin shell, asi que
        # no hay ninguna via de inyeccion desde el titulo o la URL del post.
        return subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise TranscodeError(
            "ffmpeg/ffprobe no estan instalados. La imagen Docker ya los trae; "
            "en local: winget install Gyan.FFmpeg"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TranscodeError(f"ffmpeg tardo mas de {timeout}s y se aborto") from exc


def _inspect_sync(path: Path) -> MediaProbe:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        raise TranscodeError(f"ffprobe fallo: {result.stderr.decode(errors='replace')[:300]}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TranscodeError("ffprobe devolvio una salida ilegible") from exc

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration: float | None = None
    raw_duration = payload.get("format", {}).get("duration")
    if raw_duration is not None:
        with contextlib.suppress(TypeError, ValueError):
            duration = float(raw_duration)

    return MediaProbe(
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration_seconds=duration,
        has_audio=has_audio,
    )


async def inspect(path: Path) -> MediaProbe:
    """Metadatos reales del fichero. ffprobe es bloqueante, va a un hilo."""
    return await asyncio.to_thread(_inspect_sync, path)


def _shrink_sync(source: Path, destination: Path, target_bytes: int, duration: float) -> Path:
    """Reencoda `source` intentando quedar por debajo de `target_bytes`."""
    budget_bits = target_bytes * 8 * _SIZE_SAFETY_MARGIN
    video_bitrate = int(budget_bits / duration) - _AUDIO_BITRATE_BPS

    if video_bitrate < _MIN_VIDEO_BITRATE_BPS:
        raise TranscodeError(
            f"el clip dura {duration:.0f}s y no cabe en "
            f"{target_bytes / 1_048_576:.0f} MB con calidad aceptable"
        )

    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(int(video_bitrate * 1.5)),
        "-bufsize",
        str(int(video_bitrate * 2)),
        # Se limita la altura a 720 y se fuerza dimension par, que libx264 exige.
        "-vf",
        "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:a",
        "aac",
        "-b:a",
        str(_AUDIO_BITRATE_BPS),
        "-movflags",
        "+faststart",
        str(destination),
    ]

    result = _run(command, timeout=_FFMPEG_TIMEOUT_SECONDS)
    if result.returncode != 0 or not destination.exists():
        raise TranscodeError(f"el reencode fallo: {result.stderr.decode(errors='replace')[:300]}")
    return destination


async def shrink_to_limit(source: Path, limit_bytes: int) -> Path:
    """Devuelve un fichero que cabe en `limit_bytes`.

    Si el original ya cabe se devuelve tal cual, sin tocarlo. Si no, se
    reencoda a un hermano `media-shrunk.mp4` dentro del mismo workspace y se
    borra el original: solo debe sobrevivir la copia que se va a publicar.

    Raises:
        TranscodeError: el clip no cabe ni reencodado, o ffmpeg fallo.
    """
    size = source.stat().st_size
    if size <= limit_bytes:
        return source

    probe = await inspect(source)
    duration = probe.duration_seconds
    if duration is None or duration <= 0:
        raise TranscodeError("no se pudo determinar la duracion, imposible calcular bitrate")

    destination = source.with_name("media-shrunk.mp4")
    log.info(
        "shrinking",
        original_mb=round(size / 1_048_576, 1),
        limit_mb=round(limit_bytes / 1_048_576, 1),
        duration_s=round(duration, 1),
    )

    result = await asyncio.to_thread(_shrink_sync, source, destination, limit_bytes, duration)

    final_size = result.stat().st_size
    if final_size > limit_bytes:
        raise TranscodeError(
            f"tras reencodar sigue ocupando {final_size / 1_048_576:.1f} MB, "
            f"por encima del limite de {limit_bytes / 1_048_576:.1f} MB"
        )

    # El original ya no hace falta y ocupa mas que el resultado.
    with contextlib.suppress(OSError):
        source.unlink()

    log.info("shrunk", final_mb=round(final_size / 1_048_576, 1))
    return result
