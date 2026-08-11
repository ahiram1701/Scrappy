"""Estado de deduplicacion.

Aqui NO se guarda contenido. Solo hashes, identificadores y el `file_id` que
devuelve Telegram: unos 200 bytes por item publicado. Ver `docs/EPHEMERAL_STORAGE.md`.
"""

from scrappy.storage.backends import StateBackendProtocol, build_state_backend

__all__ = ["StateBackendProtocol", "build_state_backend"]
