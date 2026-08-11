"""Jerarquia de errores de Scrappy.

La regla es simple: el pipeline distingue entre errores que afectan a *un item*
(`ItemError` y sus hijos, que se registran y se pasa al siguiente candidato) y
errores que afectan a *toda la ejecucion* (`ConfigError`, `SourceError`, que
abortan la fuente o el arranque). Esto evita que un video roto tumbe un run
entero, y a la vez evita seguir intentandolo con credenciales invalidas.
"""

from __future__ import annotations


class ScrappyError(Exception):
    """Raiz de todos los errores propios del proyecto."""


# ---------------------------------------------------------------------------
# Errores de arranque / configuracion
# ---------------------------------------------------------------------------
class ConfigError(ScrappyError):
    """La configuracion es invalida o le falta algo obligatorio."""


class MissingCredentialsError(ConfigError):
    """Una fuente esta habilitada pero no tiene credenciales."""


class ToSAcknowledgementRequiredError(ConfigError):
    """Se pidio una fuente de riesgo sin activar `enable_tos_risky_sources`.

    Ver `docs/LEGAL.md`: X-scrape, TikTok e Instagram no tienen API publica para
    este caso de uso y su utilizacion incumple los terminos de esas plataformas.
    Scrappy no las habilita por ti.
    """


class DependencyMissingError(ConfigError):
    """Falta una dependencia externa del sistema, tipicamente ffmpeg."""


# ---------------------------------------------------------------------------
# Errores de fuente (abortan esa fuente, no el run)
# ---------------------------------------------------------------------------
class SourceError(ScrappyError):
    """Fallo al descubrir contenido en una plataforma."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"[{source}] {message}")
        self.source = source


class RateLimitedError(SourceError):
    """La plataforma nos esta limitando; conviene esperar antes de reintentar."""

    def __init__(self, source: str, retry_after_seconds: float | None = None) -> None:
        detail = (
            f"rate limit alcanzado, reintentar en {retry_after_seconds:.0f}s"
            if retry_after_seconds is not None
            else "rate limit alcanzado"
        )
        super().__init__(source, detail)
        self.retry_after_seconds = retry_after_seconds


# ---------------------------------------------------------------------------
# Errores por item (se registran y el pipeline continua)
# ---------------------------------------------------------------------------
class ItemError(ScrappyError):
    """Fallo al procesar un candidato concreto."""


class DownloadError(ItemError):
    """No se pudo obtener el medio."""


class MediaTooLargeError(DownloadError):
    """El medio supera el limite configurado o el limite de subida de Telegram."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"el medio ocupa {size_bytes / 1_048_576:.1f} MB y el limite es "
            f"{limit_bytes / 1_048_576:.1f} MB"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class TranscodeError(ItemError):
    """ffmpeg fallo al normalizar el medio."""


class PublishError(ItemError):
    """Telegram rechazo la publicacion."""


class DuplicateItemError(ItemError):
    """El medio ya se habia publicado antes. No es un fallo, es el sistema funcionando."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"duplicado ({reason})")
        self.reason = reason
