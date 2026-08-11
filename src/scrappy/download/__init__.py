"""Obtencion efimera de medios.

Todo lo que se descarga aqui se borra en el mismo ciclo. El componente que lo
garantiza es `workspace.EphemeralWorkspace`; el resto del paquete se limita a
trabajar dentro de el. Ver `docs/EPHEMERAL_STORAGE.md`.
"""

from scrappy.download.downloader import Downloader
from scrappy.download.workspace import EphemeralWorkspace, sweep_orphans

__all__ = ["Downloader", "EphemeralWorkspace", "sweep_orphans"]
