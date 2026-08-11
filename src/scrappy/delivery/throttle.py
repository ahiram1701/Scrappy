"""Control de ritmo de publicacion.

Telegram limita a unos 20 mensajes por minuto en un mismo grupo o canal.
Pasarse no devuelve un error normal: devuelve un 429 con `retry_after` y, si se
insiste, puede acabar en una limitacion temporal del bot.

`python-telegram-bot` ya trae `AIORateLimiter`, que gestiona los reintentos a
nivel de peticion. Esta clase resuelve el otro lado del problema: separar en el
tiempo publicaciones consecutivas para que el canal no reciba cinco videos de
golpe, que ademas queda mal para quien lo lee.
"""

from __future__ import annotations

import asyncio
import time

from scrappy.observability.logging import get_logger

log = get_logger(__name__)


class PublishThrottle:
    """Garantiza un intervalo minimo entre publicaciones consecutivas."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._interval = max(min_interval_seconds, 0.0)
        self._last_publish: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Espera lo que falte desde la ultima publicacion.

        El lock evita que el scheduler y un `/fetch` manual simultaneos se
        pisen y acaben enviando dos mensajes en el mismo instante.
        """
        if self._interval <= 0:
            return

        async with self._lock:
            now = time.monotonic()
            if self._last_publish is not None:
                elapsed = now - self._last_publish
                remaining = self._interval - elapsed
                if remaining > 0:
                    log.debug("throttling", seconds=round(remaining, 2))
                    await asyncio.sleep(remaining)
            self._last_publish = time.monotonic()

    def reset(self) -> None:
        """Olvida la ultima publicacion. Util en tests."""
        self._last_publish = None
