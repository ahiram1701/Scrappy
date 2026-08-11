"""Modelos de dominio.

Un item atraviesa cuatro representaciones a lo largo del pipeline, y cada una
solo conoce lo que ya se ha averiguado hasta ese punto:

    RawCandidate    metadatos que devuelve una fuente. No se ha descargado nada.
    ScoredCandidate + score y su desglose. Todavia no se ha descargado nada.
    EphemeralMedia  el medio, ya en memoria o en un fichero temporal. Efimero.
    PublishedItem   lo que queda cuando el medio ya se borro: solo metadatos.

`EphemeralMedia` es deliberadamente la unica clase que toca bytes, y es
deliberadamente la unica que NO se persiste nunca. Ver `docs/EPHEMERAL_STORAGE.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Ahora, con zona horaria. Centralizado para poder falsearlo en tests."""
    return datetime.now(UTC)


class MediaKind(StrEnum):
    """Tipo de medio, que determina con que metodo de Telegram se envia."""

    VIDEO = "video"
    ANIMATION = "animation"  # GIF o video corto y mudo
    PHOTO = "photo"

    @property
    def is_video_like(self) -> bool:
        """True si el medio necesita ffmpeg/yt-dlp en vez de una descarga directa."""
        return self in (MediaKind.VIDEO, MediaKind.ANIMATION)


# ---------------------------------------------------------------------------
# 1. Descubrimiento
# ---------------------------------------------------------------------------
class RawCandidate(BaseModel):
    """Un post candidato, tal y como lo describe su plataforma de origen.

    Los adapters normalizan aqui la enorme variedad de esquemas de cada API:
    `engagement` es upvotes en Reddit, likes en X, y reproducciones en TikTok.
    Ese numero NO es comparable entre fuentes; el scorer lo convierte a
    percentil antes de usarlo (ver `ranking/scorer.py`).
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Nombre del adapter que lo encontro, p.ej. 'reddit'")
    source_id: str = Field(description="Identificador estable dentro de esa plataforma")
    permalink: str = Field(description="URL del post original, para la atribucion")

    title: str = ""
    author: str = "desconocido"
    author_url: str | None = None

    kind: MediaKind = MediaKind.VIDEO
    media_url: str | None = Field(
        default=None,
        description="URL directa del fichero. Si es None se delega en yt-dlp.",
    )
    thumbnail_url: str | None = None
    duration_seconds: float | None = None

    created_at: datetime = Field(default_factory=utcnow)
    engagement: int = Field(default=0, ge=0, description="Metrica bruta de popularidad")
    comments: int = Field(default=0, ge=0)

    nsfw: bool = False
    language: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        """Fuerza UTC: las APIs mezclan naive y aware y eso rompe las restas."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def uid(self) -> str:
        """Clave de deduplicacion barata, disponible sin descargar nada."""
        return f"{self.source}:{self.source_id}"

    def age_hours(self, *, now: datetime | None = None) -> float:
        """Horas transcurridas desde la publicacion. Nunca negativa."""
        reference = now or utcnow()
        return max((reference - self.created_at).total_seconds() / 3600.0, 0.0)


# ---------------------------------------------------------------------------
# 2. Ranking
# ---------------------------------------------------------------------------
class ScoreBreakdown(BaseModel):
    """Desglose del score, para que `--dry-run` explique por que gano cada item.

    Sin esto, calibrar los pesos seria adivinar.
    """

    model_config = ConfigDict(frozen=True)

    engagement_norm: float = 0.0
    velocity_norm: float = 0.0
    source_weight: float = 0.0
    penalties: dict[str, float] = Field(default_factory=dict)

    @property
    def penalty_total(self) -> float:
        return sum(self.penalties.values())


class ScoredCandidate(BaseModel):
    """Un candidato con su nota final. Ordenable."""

    model_config = ConfigDict(frozen=True)

    candidate: RawCandidate
    score: float
    breakdown: ScoreBreakdown

    def __lt__(self, other: ScoredCandidate) -> bool:
        return self.score < other.score


# ---------------------------------------------------------------------------
# 3. Medio efimero
# ---------------------------------------------------------------------------
class EphemeralMedia(BaseModel):
    """El medio descargado. Vive minutos y nunca se archiva.

    Puede estar en memoria (`data`, para imagenes y GIFs) o en un fichero
    temporal (`path`, para video, porque yt-dlp y ffmpeg necesitan seeking).
    Exactamente uno de los dos esta presente.

    El fichero, cuando existe, pertenece a un `EphemeralWorkspace` que lo borra
    en su `finally`. Esta clase nunca borra nada por su cuenta ni se serializa.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: RawCandidate
    kind: MediaKind
    filename: str

    path: Path | None = None
    data: bytes | None = None

    size_bytes: int = 0
    sha256: str = ""
    phash: str | None = None

    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None

    def model_post_init(self, __context: Any) -> None:
        if (self.path is None) == (self.data is None):
            raise ValueError("EphemeralMedia necesita exactamente uno de `path` o `data`")

    @property
    def in_memory(self) -> bool:
        return self.data is not None

    def read_bytes(self) -> bytes:
        """Devuelve el contenido, venga de memoria o de disco."""
        if self.data is not None:
            return self.data
        assert self.path is not None  # garantizado por model_post_init
        return self.path.read_bytes()


# ---------------------------------------------------------------------------
# 4. Lo que sobrevive
# ---------------------------------------------------------------------------
class PublishedItem(BaseModel):
    """Rastro que queda de un item una vez borrado el medio.

    Son unos 200 bytes. Sirven para (a) no repetir contenido y (b) poder
    reenviar el medio desde Telegram con `telegram_file_id` sin volver a
    descargarlo de la fuente original.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str
    permalink: str
    sha256: str
    phash: str | None = None
    score: float = 0.0
    kind: MediaKind = MediaKind.VIDEO
    telegram_message_id: int | None = None
    telegram_file_id: str | None = None
    published_at: datetime = Field(default_factory=utcnow)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"


# ---------------------------------------------------------------------------
# Resultado de una ejecucion
# ---------------------------------------------------------------------------
class RunOutcome(StrEnum):
    """Por que termino cada candidato como termino."""

    PUBLISHED = "published"
    DUPLICATE = "duplicate"
    FILTERED = "filtered"
    LOW_SCORE = "low_score"
    DOWNLOAD_FAILED = "download_failed"
    PUBLISH_FAILED = "publish_failed"


class RunReport(BaseModel):
    """Resumen de una ejecucion del pipeline, para logs, `/stats` y la CLI."""

    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    discovered: int = 0
    outcomes: dict[RunOutcome, int] = Field(default_factory=dict)
    published: list[PublishedItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    dry_run: bool = False

    def record(self, outcome: RunOutcome) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()

    def summary_line(self) -> str:
        """Una linea legible para el log y para responder a `/fetch`."""
        parts = [f"{name}={count}" for name, count in sorted(self.outcomes.items())]
        prefix = "dry-run" if self.dry_run else "run"
        return (
            f"{prefix} descubiertos={self.discovered} "
            f"{' '.join(parts) or 'sin resultados'} "
            f"en {self.duration_seconds:.1f}s"
        )
