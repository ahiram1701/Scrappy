"""Ajustes leidos del entorno (`.env` o variables reales).

Todas las variables llevan el prefijo `SCRAPPY_`. La referencia completa, con
tabla y valores por defecto, esta en `docs/CONFIGURATION.md`; `.env.example`
contiene una copia comentada lista para rellenar.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from scrappy.core.errors import ConfigError

# Limite duro de la Bot API para subir ficheros desde el bot. Con un servidor
# local de Bot API sube a 2 GB, pero eso queda fuera del alcance por defecto.
TELEGRAM_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024


class StateBackend(StrEnum):
    """Donde se guardan los metadatos de deduplicacion.

    Ninguna opcion guarda contenido: el medio siempre es efimero. Esto solo
    decide si el bot recuerda lo que ya publico entre ejecuciones.
    """

    SQLITE = "sqlite"
    MEMORY = "memory"
    NONE = "none"


class XBackend(StrEnum):
    """Como se obtiene contenido de X/Twitter.

    `API` usa la API oficial v2, que necesita tier de pago para buscar.
    `SCRAPE` usa yt-dlp con cookies: gratis, fragil y contra los ToS.
    """

    API = "api"
    SCRAPE = "scrape"


PositiveInt = Annotated[int, Field(gt=0)]


class Settings(BaseSettings):
    """Configuracion completa del proceso."""

    model_config = SettingsConfigDict(
        env_prefix="SCRAPPY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Telegram -----------------------------------------------------------
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_target_chat_id: str = ""
    telegram_admin_ids: str = Field(
        default="",
        description="IDs numericos separados por coma. Vacio = nadie puede usar comandos.",
    )

    # -- Almacenamiento efimero --------------------------------------------
    state_backend: StateBackend = StateBackend.SQLITE
    state_db_path: Path = Path("data/scrappy.db")
    workspace_root: Path | None = Field(
        default=None,
        description="Raiz de los workspaces temporales. Vacio = temp del sistema.",
    )
    max_download_mb: PositiveInt = 60

    # -- Scheduler ----------------------------------------------------------
    schedule_enabled: bool = True
    schedule_interval_minutes: PositiveInt = 180
    items_per_run: PositiveInt = 5

    # -- Reddit -------------------------------------------------------------
    # Sin credenciales: el registro de apps de Reddit se cerro en noviembre de
    # 2025 y los endpoints .json en mayo de 2026. Este adapter usa los feeds
    # Atom publicos, que no piden autenticacion pero si un User-Agent que te
    # identifique. Ver docs/adr/0009-reddit-por-rss.md
    reddit_enabled: bool = True
    reddit_user_agent: str = "python:scrappy:0.1.0 (by /u/unknown)"

    # -- Fuentes con riesgo de ToS ------------------------------------------
    enable_tos_risky_sources: bool = False

    x_enabled: bool = False
    x_backend: XBackend = XBackend.API
    x_bearer_token: SecretStr = SecretStr("")
    x_cookies_file: Path | None = None

    tiktok_enabled: bool = False
    tiktok_cookies_file: Path | None = None

    instagram_enabled: bool = False
    instagram_cookies_file: Path | None = None

    # -- Contenido ----------------------------------------------------------
    allow_nsfw: bool = False
    min_duration_seconds: float = 1.0
    max_duration_seconds: float = 180.0
    max_age_hours: float = 48.0
    phash_threshold: int = Field(default=6, ge=0, le=64)

    # -- Observabilidad -----------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # -- Rutas --------------------------------------------------------------
    sources_config_path: Path = Path("config/sources.yaml")

    # ------------------------------------------------------------------
    # Validadores
    # ------------------------------------------------------------------
    @field_validator("telegram_admin_ids")
    @classmethod
    def _strip_admin_ids(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _check_duration_range(self) -> Settings:
        if self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError("min_duration_seconds no puede ser mayor que max_duration_seconds")
        return self

    # ------------------------------------------------------------------
    # Derivados
    # ------------------------------------------------------------------
    @property
    def admin_ids(self) -> frozenset[int]:
        """IDs autorizados a usar los comandos del bot."""
        ids: set[int] = set()
        for chunk in self.telegram_admin_ids.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                ids.add(int(chunk))
            except ValueError as exc:
                raise ConfigError(
                    f"SCRAPPY_TELEGRAM_ADMIN_IDS contiene un valor no numerico: {chunk!r}"
                ) from exc
        return frozenset(ids)

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def upload_limit_bytes(self) -> int:
        """Limite efectivo de subida: el menor entre el nuestro y el de Telegram."""
        return min(self.max_download_bytes, TELEGRAM_UPLOAD_LIMIT_BYTES)

    def cookies_file_for(self, source: str) -> Path | None:
        """Fichero de cookies configurado para una fuente, si lo hay."""
        return {
            "x": self.x_cookies_file,
            "tiktok": self.tiktok_cookies_file,
            "instagram": self.instagram_cookies_file,
        }.get(source)

    # ------------------------------------------------------------------
    # Comprobaciones de arranque
    # ------------------------------------------------------------------
    def validate_for_publishing(self) -> None:
        """Falla pronto si falta lo imprescindible para publicar.

        No se ejecuta en `--dry-run`: se puede evaluar el ranking sin tener bot.
        """
        problems: list[str] = []
        if not self.telegram_bot_token.get_secret_value():
            problems.append("SCRAPPY_TELEGRAM_BOT_TOKEN esta vacio")
        if not self.telegram_target_chat_id:
            problems.append("SCRAPPY_TELEGRAM_TARGET_CHAT_ID esta vacio")
        if problems:
            raise ConfigError(
                "Configuracion de Telegram incompleta: "
                + "; ".join(problems)
                + ". Copia .env.example a .env y rellenalo."
            )

    def enabled_source_names(self) -> tuple[str, ...]:
        """Fuentes marcadas como habilitadas, sin comprobar credenciales."""
        flags = {
            "reddit": self.reddit_enabled,
            "x": self.x_enabled,
            "tiktok": self.tiktok_enabled,
            "instagram": self.instagram_enabled,
        }
        return tuple(name for name, enabled in flags.items() if enabled)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ajustes del proceso, cacheados.

    Los tests que necesiten otros valores deben construir `Settings(...)`
    directamente en vez de tocar la cache.
    """
    return Settings()
