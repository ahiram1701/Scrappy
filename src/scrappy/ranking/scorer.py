"""Puntuacion de candidatos: como se decide que es "lo mejor".

El problema central es que las metricas de las plataformas no son comparables.
12.000 upvotes en r/memes y 12.000 reproducciones en TikTok son cosas
completamente distintas, y un bot que los sume o los compare directamente
publicara siempre lo mismo: aquello que use la escala mas grande.

La solucion es no comparar nunca numeros brutos entre fuentes. Cada candidato
se convierte primero al **percentil que ocupa dentro de su propio lote**, que si
es comparable: "esta en el 10% mejor de lo que trajo Reddit hoy" y "esta en el
10% mejor de lo que trajo TikTok hoy" significan lo mismo.

    score = w_engagement * engagement_norm      percentil dentro de su lote
          + w_velocity   * velocity_norm        popularidad por hora de vida
          + w_source     * source_weight        sesgo manual por plataforma
          - penalizaciones

El termino de velocidad es el que detecta lo que esta explotando ahora mismo:
un clip con 5.000 likes en dos horas sube mas que uno con 20.000 en dos dias.

Los pesos, penalizaciones y umbrales viven en `config/sources.yaml`. La
explicacion larga, con ejemplos y consejos de calibracion, esta en
`docs/RANKING.md`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from scrappy.config.loader import RankingConfig, SourcesConfig
from scrappy.config.settings import Settings
from scrappy.core.models import RawCandidate, ScoreBreakdown, ScoredCandidate, utcnow

# Percentil que se toma como referencia del termino de velocidad. Usar el
# maximo haria que un unico item viral aplastase a todos los demas a cero.
_VELOCITY_REFERENCE_PERCENTILE = 0.90

# Se suma a la edad antes del logaritmo para que un post de cero horas no
# produzca una division por cero ni una velocidad infinita.
_AGE_OFFSET_HOURS = 2.0


class Scorer:
    """Calcula el score de un lote de candidatos."""

    #: Tope de la penalizacion por votos en contra. Sin el, cuatro pulsaciones
    #: del boton 👎 dejarian a un autor fuera para siempre, que es lo que hace
    #: el veto: esto pretende ser la version suave.
    MAX_DISLIKE_FACTOR = 3

    def __init__(
        self,
        settings: Settings,
        sources_config: SourcesConfig,
        *,
        disliked_authors: dict[str, int] | None = None,
    ) -> None:
        self._settings = settings
        self._sources = sources_config
        self._ranking: RankingConfig = sources_config.ranking
        #: Autores con votos en contra y cuantos. Lo rellena el pipeline desde
        #: el estado; vacio equivale a no haber votado nunca.
        self._disliked = {autor.lower(): votos for autor, votos in (disliked_authors or {}).items()}

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------
    def rank(
        self, candidates: list[RawCandidate], *, now: datetime | None = None
    ) -> list[ScoredCandidate]:
        """Puntua y ordena de mejor a peor.

        La normalizacion es por lote: el mismo candidato puede sacar distinta
        nota en dos ejecuciones distintas, porque compite contra otros. Es
        deliberado; lo que se quiere elegir es lo mejor *de hoy*.
        """
        if not candidates:
            return []

        reference = now or utcnow()
        by_source = _group_by_source(candidates)

        engagement_ranks = {
            source: _percentile_map([c.engagement for c in items])
            for source, items in by_source.items()
        }
        velocity_references = {
            source: _percentile_value(
                [_velocity(c, reference) for c in items], _VELOCITY_REFERENCE_PERCENTILE
            )
            for source, items in by_source.items()
        }

        scored = [
            self._score_one(
                candidate,
                reference,
                engagement_ranks[candidate.source],
                velocity_references[candidate.source],
            )
            for candidate in candidates
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def select(
        self,
        candidates: list[RawCandidate],
        *,
        limit: int,
        now: datetime | None = None,
    ) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        """Ranking mas corte por `min_score` y por `limit`.

        Returns:
            `(seleccionados, descartados_por_nota_baja)`. Los descartados se
            devuelven para poder explicarlos en `--dry-run`.
        """
        ranked = self.rank(candidates, now=now)
        minimum = self._ranking.min_score
        above = [item for item in ranked if item.score >= minimum]
        below = [item for item in ranked if item.score < minimum]
        return above[:limit], below

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------
    def _score_one(
        self,
        candidate: RawCandidate,
        now: datetime,
        engagement_ranks: dict[int, float],
        velocity_reference: float,
    ) -> ScoredCandidate:
        weights = self._ranking.weights

        engagement_norm = engagement_ranks.get(candidate.engagement, 0.0)

        velocity_norm = 0.0
        if velocity_reference > 0:
            velocity_norm = min(_velocity(candidate, now) / velocity_reference, 1.0)

        source_weight = self._sources.for_source(candidate.source).weight

        penalties = self._penalties(candidate)

        score = (
            weights.engagement * engagement_norm
            + weights.velocity * velocity_norm
            + weights.source * source_weight
            - sum(penalties.values())
        )

        return ScoredCandidate(
            candidate=candidate,
            score=max(score, 0.0),
            breakdown=ScoreBreakdown(
                engagement_norm=engagement_norm,
                velocity_norm=velocity_norm,
                source_weight=source_weight,
                penalties=penalties,
            ),
        )

    def _penalties(self, candidate: RawCandidate) -> dict[str, float]:
        """Descuentos por senales de baja calidad.

        Son restas y no multiplicadores para que su efecto sea legible: si
        `too_long` vale 0.20, un clip largo pierde exactamente 0.20 puntos.
        """
        config = self._ranking.penalties
        applied: dict[str, float] = {}

        duration = candidate.duration_seconds
        if duration is not None:
            if duration > self._settings.max_duration_seconds:
                applied["too_long"] = config.too_long
            elif duration < self._settings.min_duration_seconds:
                applied["too_short"] = config.too_short

        if not candidate.thumbnail_url:
            applied["no_thumbnail"] = config.no_thumbnail

        # Mucho like y ningun comentario suele indicar engagement inflado.
        # Solo se evalua con volumen suficiente: en un post con 50 likes, cero
        # comentarios no significa nada.
        if candidate.engagement >= 1000:
            ratio = candidate.comments / max(candidate.engagement, 1)
            if ratio < self._ranking.min_comment_ratio:
                applied["low_comment_ratio"] = config.low_comment_ratio

        # Votos en contra desde el boton 👎 de Telegram. Escala con el numero
        # de votos pero con tope: pasado ese punto lo que toca es vetar.
        votos = self._disliked.get(candidate.author.lower(), 0)
        if votos and config.disliked_author:
            factor = min(votos, self.MAX_DISLIKE_FACTOR)
            applied["disliked_author"] = config.disliked_author * factor

        return applied


# ---------------------------------------------------------------------------
# Funciones puras (faciles de testear por separado)
# ---------------------------------------------------------------------------
def _group_by_source(candidates: list[RawCandidate]) -> dict[str, list[RawCandidate]]:
    grouped: dict[str, list[RawCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.source].append(candidate)
    return dict(grouped)


def _velocity(candidate: RawCandidate, now: datetime) -> float:
    """Popularidad ajustada por lo joven que es el post.

    Se usa `log1p` en el numerador porque el engagement tiene cola muy larga:
    sin comprimirlo, un unico post con un millon de likes dejaria a todos los
    demas indistinguibles de cero.
    """
    age = candidate.age_hours(now=now) + _AGE_OFFSET_HOURS
    return math.log1p(candidate.engagement) / math.log1p(age)


def _percentile_map(values: list[int]) -> dict[int, float]:
    """Mapa valor -> percentil en [0, 1], con empates compartiendo percentil.

    Con un solo elemento devuelve 1.0: si es lo unico que trajo esa fuente, es
    lo mejor que trajo esa fuente.
    """
    if not values:
        return {}
    unique = sorted(set(values))
    if len(unique) == 1:
        return {unique[0]: 1.0}
    last = len(unique) - 1
    return {value: index / last for index, value in enumerate(unique)}


def _percentile_value(values: list[float], percentile: float) -> float:
    """Valor en un percentil dado, por interpolacion lineal."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = percentile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
