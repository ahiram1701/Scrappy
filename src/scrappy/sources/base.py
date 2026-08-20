"""Contrato que cumple toda fuente.

Un adapter tiene una sola responsabilidad: devolver metadatos de candidatos.
No descarga, no filtra, no puntua y no publica. Eso mantiene cada plataforma
aislada de las demas y hace que anadir una nueva sea un fichero, no un refactor.

Ver `docs/SOURCES.md` para la guia paso a paso.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass

import httpx

from scrappy.config.loader import SourceConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import ToSAcknowledgementRequiredError
from scrappy.core.models import RawCandidate
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# User-Agent honesto: identifica al bot y enlaza el proyecto. Falsear el
# User-Agent para simular un navegador es justo lo que no queremos hacer.
DEFAULT_USER_AGENT = "Scrappy/0.1.0 (+https://github.com/Ahiram/Scrappy)"


def rotar_objetivos(objetivos: list[str], por_ronda: int) -> list[str]:
    """Elige que objetivos toca consultar en esta ronda.

    Varias plataformas no admiten que se les consulte la lista entera cada vez
    -Reddit responde 429, X te marca la IP-, asi que se recorre por tramos. El
    desplazamiento sale de la hora actual, de modo que **rota solo entre
    ejecuciones sin necesidad de guardar estado**: con 9 objetivos y 3 por
    ronda, se cubre la lista entera cada tres rondas.

    `por_ronda` menor que 1 se trata como 1, y pedir mas de los que hay
    devuelve la lista tal cual.
    """
    if not objetivos:
        return []

    por_ronda = max(por_ronda, 1)
    if por_ronda >= len(objetivos):
        return objetivos

    bloques = max(len(objetivos) // por_ronda, 1)
    desplazamiento = (int(time.time() // 3600) % bloques) * por_ronda
    rotados = objetivos[desplazamiento:] + objetivos[:desplazamiento]
    return rotados[:por_ronda]


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """Estado de una fuente, para el comando `/sources` y para `scrappy health`."""

    name: str
    enabled: bool
    configured: bool
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.enabled and self.configured

    def render(self) -> str:
        if not self.enabled:
            return f"{self.name}: desactivada"
        if not self.configured:
            return f"{self.name}: activada pero SIN configurar - {self.detail}"
        return f"{self.name}: lista{f' ({self.detail})' if self.detail else ''}"


class SourceAdapter(abc.ABC):
    """Base de todos los adapters.

    Attributes:
        name: identificador corto, el que aparece en `sources.yaml` y en los logs.
        requires_tos_ack: si obtener contenido de esta plataforma incumple sus
            terminos de servicio. Cuando es True, la fuente queda bloqueada salvo
            que el usuario active `SCRAPPY_ENABLE_TOS_RISKY_SOURCES` a mano.
            Ver `docs/LEGAL.md`.
    """

    name: str = "abstract"
    requires_tos_ack: bool = False

    def __init__(
        self,
        settings: Settings,
        config: SourceConfig,
        client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.config = config
        self.client = client
        self.log = log.bind(source=self.name)

    # ------------------------------------------------------------------
    # A implementar por cada plataforma
    # ------------------------------------------------------------------
    @abc.abstractmethod
    async def status(self) -> SourceStatus:
        """Comprueba credenciales y dependencias, sin hacer red si se puede evitar."""

    @abc.abstractmethod
    async def discover(self, budget: int) -> list[RawCandidate]:
        """Devuelve hasta `budget` candidatos con sus metadatos.

        No debe lanzar por un item roto: lo salta y sigue. Solo lanza
        `SourceError` si la fuente entera es inutilizable (credenciales
        invalidas, plataforma caida, rate limit).
        """

    # ------------------------------------------------------------------
    # Utilidades comunes
    # ------------------------------------------------------------------
    def ensure_tos_acknowledged(self) -> None:
        """Bloquea las fuentes de riesgo salvo consentimiento explicito.

        Raises:
            ToSAcknowledgementRequiredError: si la fuente lo requiere y el flag
                no esta activado.
        """
        if self.requires_tos_ack and not self.settings.enable_tos_risky_sources:
            raise ToSAcknowledgementRequiredError(
                f"La fuente '{self.name}' obtiene contenido de una plataforma que no "
                f"ofrece API publica para este uso, por lo que incumple sus terminos "
                f"de servicio. Si aun asi quieres usarla, activa "
                f"SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true. Lee docs/LEGAL.md primero."
            )

    async def aclose(self) -> None:
        """Gancho para adapters que abran recursos propios."""
        return None
