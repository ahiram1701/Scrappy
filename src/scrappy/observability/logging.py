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
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

#: Donde van los logs cuando el proceso corre sin consola. Es el caso de la
#: tarea de autoarranque de Windows: la lanza `pythonw.exe`, que no tiene
#: ventana, y ahi `sys.stdout` es None. Sin este destino no habria ni logs ni
#: forma de saber por que dejo de publicar.
LOG_SIN_CONSOLA = Path("data/scrappy.log")

#: Corte de rotacion del log en fichero. Cinco megas es de sobra para ver que
#: paso en las ultimas rondas sin dejar el disco a merced de un proceso que
#: lleva semanas encendido.
_MAX_BYTES_LOG = 5 * 1024 * 1024

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


def configure_logging(
    level: str = "INFO",
    fmt: str = "console",
    log_file: Path | None = None,
) -> None:
    """Configura structlog y la libreria estandar de forma coherente.

    Es idempotente: llamarla dos veces (CLI + bot) no duplica handlers.

    Args:
        level: DEBUG, INFO, WARNING o ERROR.
        fmt: `console` (coloreado) o `json` (una linea por evento).
        log_file: si se indica, los logs van ahi en vez de a stdout. Sin
            consola se usa `LOG_SIN_CONSOLA` aunque no se indique nada.

    `log_file` existe por la TUI. Textual es dueno del terminal mientras corre,
    asi que un solo evento escrito en stdout pintaria basura sobre la interfaz
    y la dejaria ilegible hasta el siguiente refresco completo. Mandandolos a
    fichero se conservan enteros y la pantalla queda limpia.
    """
    # Sin consola no hay stdout al que escribir ni al que preguntarle si es un
    # tty: `sys.stdout` es None y cualquiera de las dos cosas revienta. Es
    # exactamente el caso del autoarranque, que corre con `pythonw`.
    if log_file is None and sys.stdout is None:
        log_file = LOG_SIN_CONSOLA

    handlers: list[logging.Handler] = []
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # Rotativo porque el proceso del autoarranque no termina nunca: un
        # `FileHandler` a secas crece hasta llenar el disco.
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=_MAX_BYTES_LOG,
                backupCount=3,
                encoding="utf-8",
            )
        )
    else:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
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
        # Sin color al escribir a fichero: stdout sigue siendo un tty aunque el
        # handler apunte a disco, asi que preguntarle a `isatty()` a secas
        # llenaria el fichero de escapes ANSI.
        colors = log_file is None and sys.stdout.isatty()
        processors.append(structlog.dev.ConsoleRenderer(colors=colors))

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
