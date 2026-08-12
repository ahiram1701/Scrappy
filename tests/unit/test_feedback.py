"""Pruebas de las acciones sobre publicaciones y de la migracion v2.

La prueba que mas importa es `test_la_migracion_v2_no_pierde_datos`: una
migracion que borra el historial de deduplicacion haria que el bot republicase
todo lo que ya habia publicado.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings
from scrappy.core.models import PublishedItem
from scrappy.ranking.scorer import Scorer
from scrappy.storage.backends import MemoryStateBackend, SqliteStateBackend
from tests.conftest import FIXED_NOW, make_candidate

# Esquema tal cual era en la v1, para poder migrar desde el de verdad.
_ESQUEMA_V1 = """
CREATE TABLE published (
    uid TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
    permalink TEXT NOT NULL, sha256 TEXT NOT NULL, phash TEXT,
    score REAL NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'video',
    telegram_message_id INTEGER, telegram_file_id TEXT, published_at TEXT NOT NULL
);
PRAGMA user_version=1;
"""


def _item(**extra: object) -> PublishedItem:
    base = {
        "source": "reddit",
        "source_id": "abc123",
        "permalink": "https://example.test/abc123",
        "sha256": "a" * 64,
        "author": "pepita",
    }
    base.update(extra)
    return PublishedItem(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LA prueba: la migracion no puede perder nada
# ---------------------------------------------------------------------------
async def test_la_migracion_v2_no_pierde_datos(tmp_path: Path) -> None:
    """Perder el historial haria que el bot republicase todo lo ya publicado."""
    ruta = tmp_path / "v1.db"

    # Base tal y como la dejaba la version anterior, con datos dentro.
    conexion = sqlite3.connect(ruta)
    conexion.executescript(_ESQUEMA_V1)
    conexion.execute(
        "INSERT INTO published (uid, source, source_id, permalink, sha256, "
        "score, kind, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "reddit:viejo",
            "reddit",
            "viejo",
            "https://x.test",
            "f" * 64,
            0.9,
            "video",
            "2026-08-01T10:00:00+00:00",
        ),
    )
    conexion.commit()
    conexion.close()

    backend = SqliteStateBackend(ruta)
    await backend.setup()  # aplica la v2

    assert await backend.has_uid("reddit:viejo")
    assert await backend.total_published() == 1

    # La fila antigua se queda sin autor, que es lo correcto: no se guardo y
    # no se puede inventar.
    viejo = await backend.find("reddit", "viejo")
    assert viejo is not None
    assert viejo.author == ""
    assert viejo.score == 0.9

    await backend.close()


async def test_la_migracion_es_idempotente(tmp_path: Path) -> None:
    ruta = tmp_path / "repetida.db"
    for _ in range(3):
        backend = SqliteStateBackend(ruta)
        await backend.setup()
        await backend.close()


# ---------------------------------------------------------------------------
# Buscar y opinar
# ---------------------------------------------------------------------------
@pytest.fixture(params=["memory", "sqlite"])
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> object:
    """Las dos implementaciones tienen que comportarse igual."""
    instancia = (
        MemoryStateBackend()
        if request.param == "memory"
        else SqliteStateBackend(tmp_path / "test.db")
    )
    await instancia.setup()
    yield instancia
    await instancia.close()


async def test_encuentra_un_item_publicado(backend: object) -> None:
    """Los botones solo llevan `fuente:id`; el resto se busca aqui."""
    await backend.record(_item())  # type: ignore[attr-defined]

    encontrado = await backend.find("reddit", "abc123")  # type: ignore[attr-defined]
    assert encontrado is not None
    assert encontrado.author == "pepita"

    assert await backend.find("reddit", "no-existe") is None  # type: ignore[attr-defined]


async def test_guarda_el_autor_al_publicar(backend: object) -> None:
    """Sin el autor no se puede vetar desde el boton."""
    await backend.record(_item(author="unautor"))  # type: ignore[attr-defined]
    encontrado = await backend.find("reddit", "abc123")  # type: ignore[attr-defined]
    assert encontrado is not None
    assert encontrado.author == "unautor"


async def test_cuenta_los_votos_en_contra_por_autor(backend: object) -> None:
    item = _item(author="pesado")
    await backend.record(item)  # type: ignore[attr-defined]
    await backend.record_feedback(item, "dislike")  # type: ignore[attr-defined]
    await backend.record_feedback(item, "dislike")  # type: ignore[attr-defined]

    assert await backend.disliked_authors() == {"pesado": 2}  # type: ignore[attr-defined]


async def test_borrar_y_vetar_no_cuentan_como_voto_en_contra(backend: object) -> None:
    """Son acciones distintas: vetar ya elimina al autor por completo."""
    item = _item(author="alguien")
    await backend.record_feedback(item, "deleted")  # type: ignore[attr-defined]
    await backend.record_feedback(item, "banned_author")  # type: ignore[attr-defined]

    assert await backend.disliked_authors() == {}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# El efecto en el ranking
# ---------------------------------------------------------------------------
def test_los_votos_en_contra_penalizan_a_ese_autor(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    """Sin esto el boton 👎 seria decorativo."""
    candidato = make_candidate(author="pesado", source_id="x")

    limpio = Scorer(settings, sources_config).rank([candidato], now=FIXED_NOW)[0]
    penalizado = Scorer(settings, sources_config, disliked_authors={"pesado": 1}).rank(
        [candidato], now=FIXED_NOW
    )[0]

    assert "disliked_author" in penalizado.breakdown.penalties
    assert penalizado.score < limpio.score


def test_la_penalizacion_crece_con_los_votos(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    candidato = make_candidate(author="pesado")

    uno = Scorer(settings, sources_config, disliked_authors={"pesado": 1})
    tres = Scorer(settings, sources_config, disliked_authors={"pesado": 3})

    p1 = uno.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties["disliked_author"]
    p3 = tres.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties["disliked_author"]
    assert p3 > p1


def test_la_penalizacion_tiene_tope(settings: Settings, sources_config: SourcesConfig) -> None:
    """Es la version suave del veto: no debe equivaler a eliminarlo."""
    candidato = make_candidate(author="pesado")

    tres = Scorer(settings, sources_config, disliked_authors={"pesado": 3})
    cien = Scorer(settings, sources_config, disliked_authors={"pesado": 100})

    p3 = tres.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties["disliked_author"]
    p100 = cien.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties["disliked_author"]
    assert p3 == p100


def test_no_distingue_mayusculas_en_el_autor(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    candidato = make_candidate(author="Pesado")
    scorer = Scorer(settings, sources_config, disliked_authors={"pesado": 2})

    assert "disliked_author" in scorer.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties


def test_sin_votos_no_hay_penalizacion(settings: Settings, sources_config: SourcesConfig) -> None:
    candidato = make_candidate(author="inocente")
    scorer = Scorer(settings, sources_config, disliked_authors={"otro": 5})

    assert "disliked_author" not in scorer.rank([candidato], now=FIXED_NOW)[0].breakdown.penalties
