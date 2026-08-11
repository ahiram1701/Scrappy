"""Pruebas de los tres backends de estado.

Se ejecuta el mismo juego de pruebas contra `sqlite` y `memory` para garantizar
que son intercambiables de verdad, y aparte se comprueba que `none` no recuerda
nada, que es justo lo que promete.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import PublishedItem, utcnow
from scrappy.storage.backends import (
    MemoryStateBackend,
    NullStateBackend,
    SqliteStateBackend,
    StateBackendProtocol,
    build_state_backend,
)


def _item(source: str = "reddit", source_id: str = "a1", **extra: object) -> PublishedItem:
    return PublishedItem(
        source=source,
        source_id=source_id,
        permalink=f"https://example.test/{source_id}",
        sha256=extra.pop("sha256", "0" * 64),  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture(params=["memory", "sqlite"])
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Any]:
    """Cada prueba se ejecuta dos veces, una por backend persistente."""
    instance: StateBackendProtocol
    if request.param == "memory":
        instance = MemoryStateBackend()
    else:
        instance = SqliteStateBackend(tmp_path / "test.db")
    await instance.setup()
    yield instance
    await instance.close()


async def test_recuerda_lo_publicado(backend: StateBackendProtocol) -> None:
    await backend.record(_item(source_id="abc"))
    assert await backend.has_uid("reddit:abc")
    assert not await backend.has_uid("reddit:otro")


async def test_recuerda_el_sha256(backend: StateBackendProtocol) -> None:
    await backend.record(_item(sha256="f" * 64))
    assert await backend.has_sha256("f" * 64)
    assert not await backend.has_sha256("e" * 64)


async def test_devuelve_los_phash_conocidos(backend: StateBackendProtocol) -> None:
    await backend.record(_item(source_id="con", phash="abcd1234abcd1234"))
    await backend.record(_item(source_id="sin", sha256="1" * 64))

    conocidos = await backend.known_phashes()

    assert ("reddit:con", "abcd1234abcd1234") in conocidos
    assert all(uid != "reddit:sin" for uid, _ in conocidos)


async def test_cuenta_por_fuente(backend: StateBackendProtocol) -> None:
    await backend.record(_item(source="reddit", source_id="1"))
    await backend.record(_item(source="reddit", source_id="2", sha256="2" * 64))
    await backend.record(_item(source="tiktok", source_id="3", sha256="3" * 64))

    assert await backend.stats() == {"reddit": 2, "tiktok": 1}
    assert await backend.total_published() == 3


async def test_stats_filtra_por_fecha(backend: StateBackendProtocol) -> None:
    viejo = _item(source_id="viejo", published_at=utcnow() - timedelta(days=30))
    nuevo = _item(source_id="nuevo", sha256="9" * 64)
    await backend.record(viejo)
    await backend.record(nuevo)

    recientes = await backend.stats(since=utcnow() - timedelta(days=7))
    assert recientes == {"reddit": 1}


async def test_registrar_dos_veces_el_mismo_uid_no_duplica(
    backend: StateBackendProtocol,
) -> None:
    await backend.record(_item(source_id="unico"))
    await backend.record(_item(source_id="unico", telegram_message_id=555))
    assert await backend.total_published() == 1


# ---------------------------------------------------------------------------
# Especificos
# ---------------------------------------------------------------------------
async def test_backend_none_no_recuerda_nada() -> None:
    backend = NullStateBackend()
    await backend.setup()

    await backend.record(_item())

    assert not await backend.has_uid("reddit:a1")
    assert await backend.total_published() == 0
    assert await backend.known_phashes() == []
    await backend.close()


async def test_sqlite_persiste_entre_conexiones(tmp_path: Path) -> None:
    """El punto de sqlite: recordar entre ejecuciones del proceso."""
    ruta = tmp_path / "persistente.db"

    primero = SqliteStateBackend(ruta)
    await primero.setup()
    await primero.record(_item(source_id="persiste"))
    await primero.close()

    segundo = SqliteStateBackend(ruta)
    await segundo.setup()
    assert await segundo.has_uid("reddit:persiste")
    await segundo.close()


async def test_sqlite_crea_el_directorio(tmp_path: Path) -> None:
    backend = SqliteStateBackend(tmp_path / "sub" / "dir" / "estado.db")
    await backend.setup()
    assert (tmp_path / "sub" / "dir").is_dir()
    await backend.close()


async def test_las_migraciones_son_idempotentes(tmp_path: Path) -> None:
    ruta = tmp_path / "migraciones.db"
    for _ in range(3):
        backend = SqliteStateBackend(ruta)
        await backend.setup()
        await backend.close()
    # Si `PRAGMA user_version` no funcionase, la segunda pasada fallaria.


async def test_memory_olvida_al_cerrar() -> None:
    backend = MemoryStateBackend()
    await backend.setup()
    await backend.record(_item())
    await backend.close()
    assert await backend.total_published() == 0


def test_la_factoria_respeta_la_configuracion(tmp_path: Path) -> None:
    base = {"state_db_path": tmp_path / "x.db"}
    assert isinstance(
        build_state_backend(Settings(state_backend=StateBackend.SQLITE, **base)),
        SqliteStateBackend,
    )
    assert isinstance(
        build_state_backend(Settings(state_backend=StateBackend.MEMORY, **base)),
        MemoryStateBackend,
    )
    assert isinstance(
        build_state_backend(Settings(state_backend=StateBackend.NONE, **base)),
        NullStateBackend,
    )
