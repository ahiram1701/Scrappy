"""Scrappy: curador de videos cortos y memes hacia Telegram.

El contenido descargado es siempre efimero: vive en un workspace temporal el
tiempo justo para publicarse en Telegram y se borra en el mismo ciclo. Lo unico
que puede persistir son metadatos de deduplicacion. Ver `docs/EPHEMERAL_STORAGE.md`.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
