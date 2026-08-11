"""Publicacion en Telegram.

Telegram no es solo el destino: es el unico archivo del contenido. Lo que se
publica aqui es lo unico que sobrevive al ciclo de vida del medio.
"""

from scrappy.delivery.captions import build_caption
from scrappy.delivery.publisher import TelegramPublisher

__all__ = ["TelegramPublisher", "build_caption"]
