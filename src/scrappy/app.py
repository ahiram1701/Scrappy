"""Composition root: donde se construye y se conecta todo.

El resto del codigo no crea sus dependencias, las recibe. Eso mantiene cada
pieza testeable con dobles y concentra en un solo fichero el orden de arranque
y, sobre todo, el orden de apagado, que es donde se cierran conexiones y se
purgan los workspaces.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any

import httpx
from telegram import Bot
from telegram.ext import AIORateLimiter, ExtBot

from scrappy.config.loader import SourcesConfig, load_sources_config
from scrappy.config.settings import Settings
from scrappy.core.errors import ConfigError
from scrappy.core.models import RunReport
from scrappy.core.pipeline import Pipeline
from scrappy.delivery.publisher import TelegramPublisher
from scrappy.download.downloader import Downloader
from scrappy.download.workspace import (
    install_signal_handlers,
    iter_active,
    purge_active,
    sweep_orphans,
)
from scrappy.download.ytdlp_engine import ffmpeg_available
from scrappy.observability.logging import configure_logging, get_logger
from scrappy.sources.base import SourceAdapter, SourceStatus
from scrappy.sources.registry import build_adapters, build_http_client
from scrappy.storage.backends import StateBackendProtocol, build_state_backend
from scrappy.storage.dedup import Deduplicator

log = get_logger(__name__)


@dataclass(slots=True)
class HealthReport:
    """Diagnostico del sistema. Lo consumen `/health` y `scrappy health`."""

    ffmpeg: bool
    state_backend: str
    state_items: int
    telegram_configured: bool
    active_workspaces: int
    sources: list[SourceStatus]

    @property
    def ok(self) -> bool:
        return (
            self.ffmpeg
            and self.active_workspaces == 0
            and any(status.usable for status in self.sources)
        )

    def render(self) -> str:
        """Texto plano, valido tanto para la terminal como para Telegram."""
        lines = [
            f"ffmpeg: {'OK' if self.ffmpeg else 'NO ENCONTRADO'}",
            f"estado: {self.state_backend} ({self.state_items} items recordados)",
            f"telegram: {'configurado' if self.telegram_configured else 'SIN configurar'}",
            f"workspaces activos: {self.active_workspaces} "
            f"({'limpio' if self.active_workspaces == 0 else 'hay descargas en curso'})",
            "fuentes:",
        ]
        lines += [f"  - {status.render()}" for status in self.sources]
        return "\n".join(lines)


class ScrappyApp:
    """Todas las piezas montadas y listas para usar.

    Se usa como gestor de contexto asincrono para garantizar el apagado:

        async with await ScrappyApp.create(settings) as app:
            await app.run_pipeline()
    """

    def __init__(
        self,
        *,
        settings: Settings,
        sources_config: SourcesConfig,
        client: httpx.AsyncClient,
        state: StateBackendProtocol,
        adapters: list[SourceAdapter],
        pipeline: Pipeline,
        bot: Bot | None,
    ) -> None:
        self.settings = settings
        self.sources_config = sources_config
        self.client = client
        self.state = state
        self.adapters = adapters
        self.pipeline = pipeline
        self.bot = bot
        self.paused = False

    # ------------------------------------------------------------------
    # Construccion
    # ------------------------------------------------------------------
    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        only_source: str | None = None,
        with_publisher: bool = True,
    ) -> ScrappyApp:
        """Monta la aplicacion.

        Args:
            only_source: limita el pipeline a una sola fuente.
            with_publisher: si False no se construye el bot ni se exige token,
                que es lo que permite ejecutar `--dry-run` sin credenciales de
                Telegram.
        """
        configure_logging(settings.log_level, settings.log_format)
        install_signal_handlers()

        # Limpia lo que un crash anterior pudiera haber dejado en el disco.
        sweep_orphans(settings.workspace_root)

        sources_config = load_sources_config(settings.sources_config_path)
        client = build_http_client()

        state = build_state_backend(settings)
        await state.setup()

        adapters = build_adapters(settings, sources_config, client, only=only_source)

        bot: Bot | None = None
        publisher: TelegramPublisher | None = None
        if with_publisher:
            settings.validate_for_publishing()
            bot = ExtBot(
                token=settings.telegram_bot_token.get_secret_value(),
                rate_limiter=AIORateLimiter(),
            )
            await bot.initialize()
            publisher = TelegramPublisher(
                bot, settings.telegram_target_chat_id, sources_config.delivery
            )

        pipeline = Pipeline(
            settings=settings,
            sources_config=sources_config,
            adapters=adapters,
            downloader=Downloader(settings, client),
            deduplicator=Deduplicator(state, phash_threshold=settings.phash_threshold),
            state=state,
            publisher=publisher,
        )

        if not ffmpeg_available():
            log.warning(
                "ffmpeg_missing",
                detail="los videos fallaran al descargarse. La imagen Docker lo incluye; "
                "en Windows: winget install Gyan.FFmpeg",
            )

        return cls(
            settings=settings,
            sources_config=sources_config,
            client=client,
            state=state,
            adapters=adapters,
            pipeline=pipeline,
            bot=bot,
        )

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def __aenter__(self) -> ScrappyApp:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Apagado ordenado. Lo ultimo que se hace es asegurar el disco limpio."""
        for adapter in self.adapters:
            await adapter.aclose()
        await self.client.aclose()
        await self.state.close()
        if self.bot is not None:
            await self.bot.shutdown()
        purge_active()

    # ------------------------------------------------------------------
    # Operaciones
    # ------------------------------------------------------------------
    async def run_pipeline(self, *, limit: int | None = None, dry_run: bool = False) -> RunReport:
        return await self.pipeline.run(limit=limit, dry_run=dry_run)

    async def source_statuses(self) -> list[SourceStatus]:
        """Estado de todas las fuentes conocidas, no solo de las construidas."""
        statuses = [await adapter.status() for adapter in self.adapters]
        built = {status.name for status in statuses}

        for name in ("reddit", "lemmy", "bluesky", "x", "tiktok", "instagram"):
            if name in built:
                continue
            enabled = bool(getattr(self.settings, f"{name}_enabled", False))
            detail = (
                "bloqueada: requiere SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true"
                if enabled
                else "desactivada en el .env"
            )
            statuses.append(
                SourceStatus(name=name, enabled=enabled, configured=False, detail=detail)
            )
        return statuses

    async def health(self) -> HealthReport:
        return HealthReport(
            ffmpeg=ffmpeg_available(),
            state_backend=str(self.settings.state_backend),
            state_items=await self.state.total_published(),
            telegram_configured=bool(
                self.settings.telegram_bot_token.get_secret_value()
                and self.settings.telegram_target_chat_id
            ),
            active_workspaces=len(list(iter_active())),
            sources=await self.source_statuses(),
        )

    def effective_config(self) -> dict[str, Any]:
        """Configuracion visible, con los secretos ya redactados.

        Lo consume `/config`, que responde en un chat: aqui no puede colarse
        ningun token.
        """
        settings = self.settings
        return {
            "state_backend": str(settings.state_backend),
            "workspace_root": str(settings.workspace_root or "temporal del sistema"),
            "items_per_run": settings.items_per_run,
            "schedule_enabled": settings.schedule_enabled,
            "schedule_interval_minutes": settings.schedule_interval_minutes,
            "max_download_mb": settings.max_download_mb,
            "allow_nsfw": settings.allow_nsfw,
            "max_age_hours": settings.max_age_hours,
            "duration_range_s": [
                settings.min_duration_seconds,
                settings.max_duration_seconds,
            ],
            "phash_threshold": settings.phash_threshold,
            "enable_tos_risky_sources": settings.enable_tos_risky_sources,
            "x_backend": str(settings.x_backend),
            "min_score": self.sources_config.ranking.min_score,
            "ranking_weights": self.sources_config.ranking.weights.model_dump(),
            "telegram_bot_token": "***",
            "telegram_target_chat_id": settings.telegram_target_chat_id,
            "admins": len(settings.admin_ids),
        }


def load_settings_or_die() -> Settings:
    """Carga los ajustes convirtiendo un error de validacion en un mensaje util."""
    try:
        return Settings()
    except ValueError as exc:
        raise ConfigError(
            f"La configuracion del entorno no es valida:\n{exc}\n"
            "Revisa tu fichero .env contra .env.example."
        ) from exc
