"""Filtros duros, aplicados sobre metadatos antes de descargar nada.

La diferencia con el scorer es intencionada: aqui se decide si un item es
*aceptable*, no si es *bueno*. Un video de tres horas no es un video corto
mediocre, sencillamente no es lo que este bot publica, y no tiene sentido
puntuarlo para luego descartarlo.

Todo lo que se filtra aqui se ahorra una descarga, que es el paso caro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from scrappy.config.loader import FiltersConfig
from scrappy.config.settings import Settings
from scrappy.core.models import RawCandidate, utcnow


@dataclass(frozen=True, slots=True)
class FilterVerdict:
    """Resultado de evaluar un candidato."""

    accepted: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> FilterVerdict:
        return cls(accepted=True)

    @classmethod
    def reject(cls, reason: str) -> FilterVerdict:
        return cls(accepted=False, reason=reason)


class CandidateFilter:
    """Aplica las reglas duras de `.env` y de la seccion `filters` del YAML."""

    def __init__(self, settings: Settings, config: FiltersConfig) -> None:
        self._settings = settings
        self._config = config

    def evaluate(self, candidate: RawCandidate, *, now: datetime | None = None) -> FilterVerdict:
        """Decide si el candidato sigue en el pipeline.

        El orden va de lo mas barato y mas frecuente a lo menos, para salir
        cuanto antes.
        """
        reference = now or utcnow()

        if candidate.nsfw and not self._settings.allow_nsfw:
            return FilterVerdict.reject("nsfw")

        age = candidate.age_hours(now=reference)
        if age > self._settings.max_age_hours:
            return FilterVerdict.reject(f"demasiado antiguo ({age:.0f}h)")

        duration = candidate.duration_seconds
        if duration is not None:
            if duration < self._settings.min_duration_seconds:
                return FilterVerdict.reject(f"demasiado corto ({duration:.1f}s)")
            if duration > self._settings.max_duration_seconds:
                return FilterVerdict.reject(f"demasiado largo ({duration:.0f}s)")

        author = candidate.author.lower().lstrip("@")
        if author in self._config.blocked_authors:
            return FilterVerdict.reject(f"autor vetado ({candidate.author})")

        haystack = candidate.title.lower()
        for keyword in self._config.blocked_keywords:
            if keyword in haystack:
                return FilterVerdict.reject(f"palabra vetada ({keyword})")

        allowed = self._config.allowed_languages
        if allowed and candidate.language and candidate.language.lower() not in allowed:
            return FilterVerdict.reject(f"idioma no permitido ({candidate.language})")

        return FilterVerdict.ok()

    def partition(
        self, candidates: list[RawCandidate], *, now: datetime | None = None
    ) -> tuple[list[RawCandidate], list[tuple[RawCandidate, str]]]:
        """Separa aceptados de rechazados, conservando el motivo de cada rechazo.

        Los motivos se muestran en `--dry-run`, que es como se calibran estos
        filtros sin publicar nada.
        """
        accepted: list[RawCandidate] = []
        rejected: list[tuple[RawCandidate, str]] = []
        for candidate in candidates:
            verdict = self.evaluate(candidate, now=now)
            if verdict.accepted:
                accepted.append(candidate)
            else:
                rejected.append((candidate, verdict.reason))
        return accepted, rejected
