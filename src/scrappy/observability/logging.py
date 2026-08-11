"""Logging estructurado con structlog.

Dos formatos: `console` (coloreado, para desarrollo) y `json` (una linea por
evento, para produccion y para poder hacer `jq` sobre los logs del contenedor).

Los eventos llevan siempre el nombre de la fuente y el uid del item cuando
aplica, de modo que se puede seguir la pista de un meme concreto a lo largo de
todo el pipeline con un solo grep.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_REDACTED = "***"
_SECRET_KEYS = frozenset(
    {
        "token",
        "bot_token",
        "client_secret",
        "bearer_token",
        "password",
        "authorization",
        "cookie",
        "cookies",
    }
)


def _redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Evita que un token acabe en los logs por un `log.info(..., **config)` descuidado."""
    for key in list(event_dict):
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Configura structlog y la libreria estandar de forma coherente.

    Es idempotente: llamarla dos veces (CLI + bot) no duplica handlers.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    # httpx y apscheduler son muy ruidosos en INFO y no aportan nada util aqui.
    for noisy in ("httpx", "httpcore", "apscheduler", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Logger ligado al modulo que lo pide."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
