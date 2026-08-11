"""Seleccion de lo mejor: filtros duros primero, ranking por score despues."""

from scrappy.ranking.filters import CandidateFilter, FilterVerdict
from scrappy.ranking.scorer import Scorer

__all__ = ["CandidateFilter", "FilterVerdict", "Scorer"]
