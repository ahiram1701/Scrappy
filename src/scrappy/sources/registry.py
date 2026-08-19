"""Registro de adapters.

Un solo sitio donde se dice que fuentes existen y como se construyen. Anadir
una plataforma nueva es escribir su fichero y anadir una linea a `_FACTORIES`;
nada mas del pipeline necesita enterarse. Ver `docs/SOURCES.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings, XBackend
from scrappy.core.errors import ToSAcknowledgementRequiredError
from scrappy.observability.logging import get_logger
from scrappy.sources.base import DEFAULT_USER_AGENT, SourceAdapter
from scrappy.sources.bluesky import BlueskySource
from scrappy.sources.giphy import GiphySource
from scrappy.sources.imgur import ImgurSource
from scrappy.sources.instagram import InstagramSource
from scrappy.sources.lemmy import LemmySource
from scrappy.sources.reddit import RedditSource
from scrappy.sources.tiktok import TikTokSource
from scrappy.sources.x import build_x_source
from scrappy.sources.youtube import YouTubeSource

log = get_logger(__name__)

AdapterFactory = Callable[..., SourceAdapter]

_FACTORIES: dict[str, AdapterFactory] = {
    "reddit": RedditSource,
    "lemmy": LemmySource,
    "bluesky": BlueskySource,
    "imgur": ImgurSource,
    "giphy": GiphySource,
    "youtube": YouTubeSource,
    "x": build_x_source,
    "tiktok": TikTokSource,
    "instagram": InstagramSource,
}


def iter_adapter_names() -> Iterator[str]:
    """Nombres de todas las fuentes conocidas, esten activas o no."""
    yield from _FACTORIES


def requiere_ack_de_tos(nombre: str, settings: Settings) -> bool:
    """Si esa fuente esta detras del flag de aviso legal.

    Se responde sin construir el adapter porque contestar a un boton de
    Telegram no deberia montar un cliente HTTP ni leer credenciales.

    `x` es el caso raro: su entrada de `_FACTORIES` no es una clase sino una
    factoria que elige entre la API oficial -uso permitido- y el scrape, que
    no lo es, segun `SCRAPPY_X_BACKEND`.
    """
    if nombre == "x":
        return settings.x_backend != XBackend.API
    return bool(getattr(_FACTORIES.get(nombre), "requires_tos_ack", False))


def build_http_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """Cliente HTTP compartido por todos los adapters.

    Compartirlo mantiene un unico pool de conexiones y un unico sitio donde
    ajustar timeouts y User-Agent.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )


def build_adapters(
    settings: Settings,
    sources_config: SourcesConfig,
    client: httpx.AsyncClient,
    *,
    only: str | None = None,
) -> list[SourceAdapter]:
    """Construye los adapters habilitados.

    Args:
        only: si se indica, se construye solo esa fuente (lo usa `--source` de
            la CLI y `/fetch reddit` del bot), aunque este desactivada en el
            `.env`: pedirla explicitamente es intencion suficiente.

    Las fuentes bloqueadas por falta de consentimiento de ToS se omiten con un
    aviso en vez de reventar el arranque, para que tener `tiktok_enabled=true`
    sin el flag no impida que Reddit siga funcionando.
    """
    enabled = set(settings.enabled_source_names())
    if only is not None:
        if only not in _FACTORIES:
            known = ", ".join(sorted(_FACTORIES))
            raise ValueError(f"fuente desconocida: {only!r}. Conocidas: {known}")
        enabled = {only}

    adapters: list[SourceAdapter] = []
    for name in _FACTORIES:
        if name not in enabled:
            continue
        adapter = _FACTORIES[name](settings, sources_config.for_source(name), client)
        try:
            adapter.ensure_tos_acknowledged()
        except ToSAcknowledgementRequiredError as exc:
            log.warning("source_blocked", source=name, reason=str(exc))
            continue
        adapters.append(adapter)

    if not adapters:
        log.warning(
            "no_sources_enabled",
            detail="ninguna fuente utilizable; revisa SCRAPPY_*_ENABLED y config/sources.yaml",
        )
    return adapters
