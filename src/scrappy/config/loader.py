"""Carga y validacion de `config/sources.yaml`.

Este fichero es el catalogo editable: que subreddits, que hashtags, cuanto pesa
cada cosa en el ranking. Se valida con pydantic al arrancar para que un typo en
el YAML de un error claro en el arranque, no un `KeyError` tres etapas despues.

Si el fichero no existe se usan los valores por defecto de estos modelos, de
modo que `scrappy fetch --dry-run` funciona sin configuracion previa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scrappy.core.errors import ConfigError


class RankingWeights(BaseModel):
    """Pesos de los tres terminos del score. Deberian sumar 1.0."""

    model_config = ConfigDict(extra="forbid")

    engagement: float = Field(default=0.50, ge=0.0, le=1.0)
    velocity: float = Field(default=0.35, ge=0.0, le=1.0)
    source: float = Field(default=0.15, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _warn_if_not_normalized(self) -> RankingWeights:
        total = self.engagement + self.velocity + self.source
        # Se tolera desviacion porque el score no necesita estar en [0,1] para
        # ordenar; solo se rechaza el caso degenerado de todo a cero.
        if total <= 0:
            raise ValueError("los pesos de ranking no pueden sumar cero")
        return self


class RankingPenalties(BaseModel):
    """Cuanto se resta del score por cada defecto detectado."""

    model_config = ConfigDict(extra="forbid")

    too_long: float = Field(default=0.20, ge=0.0)
    too_short: float = Field(default=0.10, ge=0.0)
    no_thumbnail: float = Field(default=0.05, ge=0.0)
    low_comment_ratio: float = Field(default=0.10, ge=0.0)


class RankingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: RankingWeights = Field(default_factory=RankingWeights)
    penalties: RankingPenalties = Field(default_factory=RankingPenalties)
    min_comment_ratio: float = Field(default=0.002, ge=0.0)
    min_score: float = Field(default=0.35, ge=0.0)


class SourceConfig(BaseModel):
    """Ajustes comunes a toda fuente. Cada adapter lee ademas sus propias claves."""

    model_config = ConfigDict(extra="allow")

    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    budget: int = Field(default=30, gt=0, description="Candidatos a pedir antes de filtrar")

    def get_list(self, key: str) -> list[str]:
        """Lee una clave de lista de strings tolerando ausencia y valores sueltos."""
        raw = (self.model_extra or {}).get(key)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item) for item in raw]
        raise ConfigError(f"`{key}` deberia ser una lista, no {type(raw).__name__}")

    def get_int(self, key: str, default: int) -> int:
        raw = (self.model_extra or {}).get(key, default)
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"`{key}` deberia ser un entero, no {raw!r}") from exc

    def get_str(self, key: str, default: str) -> str:
        raw = (self.model_extra or {}).get(key, default)
        return str(raw)


class FiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocked_keywords: list[str] = Field(default_factory=list)
    blocked_authors: list[str] = Field(default_factory=list)
    allowed_languages: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> FiltersConfig:
        # Se comparan siempre en minusculas; normalizar aqui evita repetirlo
        # en cada llamada del filtro.
        object.__setattr__(self, "blocked_keywords", [k.lower() for k in self.blocked_keywords])
        object.__setattr__(self, "blocked_authors", [a.lower() for a in self.blocked_authors])
        object.__setattr__(self, "allowed_languages", [a.lower() for a in self.allowed_languages])
        return self


class DeliveryConfig(BaseModel):
    """Presentacion en Telegram.

    No hay opcion para desactivar la atribucion: republicar trabajo ajeno sin
    acreditar al autor no es negociable. Ver `docs/LEGAL.md`.
    """

    model_config = ConfigDict(extra="forbid")

    show_score: bool = False
    show_source_badge: bool = True
    silent_notifications: bool = False
    delay_between_posts: float = Field(default=4.0, ge=0.0)


class SourcesConfig(BaseModel):
    """El fichero `sources.yaml` completo, ya validado."""

    model_config = ConfigDict(extra="forbid")

    ranking: RankingConfig = Field(default_factory=RankingConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)

    def for_source(self, name: str) -> SourceConfig:
        """Ajustes de una fuente, con valores por defecto si no esta en el YAML."""
        return self.sources.get(name, SourceConfig())


def load_sources_config(path: Path) -> SourcesConfig:
    """Lee y valida el YAML de fuentes.

    Si el fichero no existe se devuelven los valores por defecto: asi el
    proyecto arranca recien clonado, sin obligar a copiar el ejemplo primero.

    Raises:
        ConfigError: el YAML esta mal formado o no cumple el esquema.
    """
    if not path.exists():
        return SourcesConfig()

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} no es YAML valido: {exc}") from exc

    if raw is None:
        return SourcesConfig()
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} deberia contener un mapa en la raiz, no {type(raw).__name__}")

    try:
        return SourcesConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"{path} no cumple el esquema esperado:\n{exc}") from exc
