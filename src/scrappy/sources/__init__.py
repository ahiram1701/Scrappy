"""Adapters de fuentes.

Cada plataforma se encapsula en una clase que solo tiene que saber devolver
`RawCandidate`s. El resto del pipeline no sabe de que plataforma vienen.

Para anadir una fuente nueva basta con implementar `SourceAdapter` y
registrarla: ver `docs/SOURCES.md`.
"""

from scrappy.sources.base import SourceAdapter, SourceStatus
from scrappy.sources.registry import build_adapters, iter_adapter_names

__all__ = ["SourceAdapter", "SourceStatus", "build_adapters", "iter_adapter_names"]
