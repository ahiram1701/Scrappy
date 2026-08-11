"""Pruebas del control de ritmo.

Telegram limita a ~20 mensajes por minuto y por chat. Ademas de evitar el 429,
separar las publicaciones hace que el canal se lea mejor: cinco videos de golpe
quedan mal.
"""

from __future__ import annotations

import asyncio
import time

from scrappy.delivery.throttle import PublishThrottle


async def test_la_primera_publicacion_no_espera() -> None:
    throttle = PublishThrottle(5.0)

    inicio = time.monotonic()
    await throttle.wait()

    assert time.monotonic() - inicio < 0.5


async def test_la_segunda_espera_el_intervalo() -> None:
    throttle = PublishThrottle(0.3)
    await throttle.wait()

    inicio = time.monotonic()
    await throttle.wait()

    assert time.monotonic() - inicio >= 0.25


async def test_un_intervalo_de_cero_no_espera_nunca() -> None:
    throttle = PublishThrottle(0.0)

    inicio = time.monotonic()
    for _ in range(20):
        await throttle.wait()

    assert time.monotonic() - inicio < 0.5


async def test_dos_llamadas_concurrentes_se_serializan() -> None:
    """El caso real: el scheduler y un `/fetch` manual coinciden en el tiempo.

    Sin el lock, las dos verian el mismo `_last_publish` y publicarian a la vez.
    """
    throttle = PublishThrottle(0.2)
    await throttle.wait()

    inicio = time.monotonic()
    await asyncio.gather(throttle.wait(), throttle.wait())
    transcurrido = time.monotonic() - inicio

    # Dos esperas encadenadas, no dos en paralelo.
    assert transcurrido >= 0.35


async def test_reset_olvida_la_ultima_publicacion() -> None:
    throttle = PublishThrottle(10.0)
    await throttle.wait()
    throttle.reset()

    inicio = time.monotonic()
    await throttle.wait()

    assert time.monotonic() - inicio < 0.5


def test_un_intervalo_negativo_se_trata_como_cero() -> None:
    assert PublishThrottle(-5.0)._interval == 0.0
