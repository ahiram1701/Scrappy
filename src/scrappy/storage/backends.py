"""Backends de estado intercambiables.

El pipeline nunca sabe cual esta activo: habla con `StateBackendProtocol` y ya.
Eso permite ofrecer tres politicas de privacidad sin ramificar la logica:

    sqlite  metadatos en disco. Dedup real entre ejecuciones. Por defecto.
    memory  todo en RAM. Nada toca el disco; el bot puede repetir entre runs.
    none    sin dedup. Maxima privacidad, maxima repeticion.

Insisto en lo importante: ninguno guarda el medio. El medio siempre se borra.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiosqlite

from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import PublishedItem, utcnow
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

# Esquema versionado. Cada entrada se aplica en orden si `user_version` es menor.
# Se prefiere esto a Alembic porque son cuatro sentencias y no compensa la
# dependencia (ver docs/adr/0004-sqlite-sin-orm.md).
_MIGRATIONS: tuple[str, ...] = (
    # v1 -- tabla base
    """
    CREATE TABLE IF NOT EXISTS published (
        uid                 TEXT PRIMARY KEY,
        source              TEXT NOT NULL,
        source_id           TEXT NOT NULL,
        permalink           TEXT NOT NULL,
        sha256              TEXT NOT NULL,
        phash               TEXT,
        score               REAL NOT NULL DEFAULT 0,
        kind                TEXT NOT NULL DEFAULT 'video',
        telegram_message_id INTEGER,
        telegram_file_id    TEXT,
        published_at        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_published_sha256 ON published(sha256);
    CREATE INDEX IF NOT EXISTS idx_published_at     ON published(published_at);
    CREATE INDEX IF NOT EXISTS idx_published_source ON published(source);
    """,
    # v2 -- opiniones desde los botones de Telegram
    #
    # `author` se anade a `published` porque el `callback_data` de Telegram no
    # da para llevarlo (64 bytes) y hace falta para el boton de vetar autor.
    # Las filas antiguas se quedan con la cadena vacia, que es correcto: de
    # ellas no se guardo el autor y no se puede inventar.
    """
    ALTER TABLE published ADD COLUMN author TEXT NOT NULL DEFAULT '';

    CREATE TABLE IF NOT EXISTS feedback (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        TEXT NOT NULL,
        source     TEXT NOT NULL,
        author     TEXT NOT NULL DEFAULT '',
        kind       TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_feedback_author ON feedback(author);
    CREATE INDEX IF NOT EXISTS idx_feedback_kind   ON feedback(kind);
    """,
)


@runtime_checkable
class StateBackendProtocol(Protocol):
    """Lo que el pipeline necesita saber del estado. Nada mas."""

    async def setup(self) -> None: ...

    async def close(self) -> None: ...

    async def has_uid(self, uid: str) -> bool:
        """True si ese `source:source_id` ya se publico."""
        ...

    async def has_sha256(self, digest: str) -> bool:
        """True si ya se publico un fichero byte a byte identico."""
        ...

    async def known_phashes(self, *, within_days: int = 90) -> list[tuple[str, str]]:
        """Pares `(uid, phash)` recientes, para comparar por distancia de Hamming."""
        ...

    async def record(self, item: PublishedItem) -> None: ...

    async def stats(self, *, since: datetime | None = None) -> dict[str, int]:
        """Publicados por fuente desde una fecha."""
        ...

    async def total_published(self) -> int: ...

    async def find(self, source: str, source_id: str) -> PublishedItem | None:
        """Recupera un item publicado. Lo usan los botones de Telegram."""
        ...

    async def record_feedback(self, item: PublishedItem, kind: str) -> None:
        """Guarda una opinion sobre un item ya publicado."""
        ...

    async def disliked_authors(self) -> dict[str, int]:
        """Autores con votos negativos y cuantos, para penalizarlos."""
        ...


class NullStateBackend:
    """No recuerda nada. El bot repetira contenido; es el precio de no guardar."""

    name = "none"

    async def setup(self) -> None:
        log.warning(
            "state_backend_none",
            detail="dedup desactivado: el bot puede publicar el mismo meme varias veces",
        )

    async def close(self) -> None:
        return None

    async def has_uid(self, uid: str) -> bool:
        return False

    async def has_sha256(self, digest: str) -> bool:
        return False

    async def known_phashes(self, *, within_days: int = 90) -> list[tuple[str, str]]:
        return []

    async def record(self, item: PublishedItem) -> None:
        return None

    async def stats(self, *, since: datetime | None = None) -> dict[str, int]:
        return {}

    async def total_published(self) -> int:
        return 0

    async def find(self, source: str, source_id: str) -> PublishedItem | None:
        return None

    async def record_feedback(self, item: PublishedItem, kind: str) -> None:
        return None

    async def disliked_authors(self) -> dict[str, int]:
        return {}


class MemoryStateBackend:
    """Dedup dentro de una misma ejecucion, sin tocar el disco.

    Es la opcion honesta para quien no quiere ningun rastro en la maquina: el
    proceso recuerda lo que publico mientras vive y lo olvida al terminar.
    """

    name = "memory"

    def __init__(self) -> None:
        self._items: dict[str, PublishedItem] = {}
        self._feedback: list[tuple[str, str]] = []

    async def setup(self) -> None:
        return None

    async def close(self) -> None:
        self._items.clear()
        self._feedback.clear()

    async def has_uid(self, uid: str) -> bool:
        return uid in self._items

    async def has_sha256(self, digest: str) -> bool:
        return any(item.sha256 == digest for item in self._items.values())

    async def known_phashes(self, *, within_days: int = 90) -> list[tuple[str, str]]:
        return [(uid, item.phash) for uid, item in self._items.items() if item.phash is not None]

    async def record(self, item: PublishedItem) -> None:
        self._items[item.uid] = item

    async def stats(self, *, since: datetime | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._items.values():
            if since is not None and item.published_at < since:
                continue
            counts[item.source] = counts.get(item.source, 0) + 1
        return counts

    async def total_published(self) -> int:
        return len(self._items)

    async def find(self, source: str, source_id: str) -> PublishedItem | None:
        return self._items.get(f"{source}:{source_id}")

    async def record_feedback(self, item: PublishedItem, kind: str) -> None:
        self._feedback.append((item.author, kind))

    async def disliked_authors(self) -> dict[str, int]:
        cuenta: dict[str, int] = {}
        for autor, kind in self._feedback:
            if kind == "dislike" and autor:
                cuenta[autor] = cuenta.get(autor, 0) + 1
        return cuenta


class SqliteStateBackend:
    """Metadatos en un SQLite local. Opcion por defecto.

    El fichero crece del orden de 200 bytes por item publicado: publicando 5
    memes cada 3 horas son ~3 MB al ano. No contiene ningun byte de medio.
    """

    name = "sqlite"

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        # WAL evita bloqueos entre el scheduler y los comandos del bot, que
        # corren en el mismo proceso pero en tareas distintas.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        log.info("state_backend_ready", backend="sqlite", path=str(self._path))

    async def _migrate(self) -> None:
        db = self._require_db()
        async with db.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        current = int(row[0]) if row else 0

        for version, script in enumerate(_MIGRATIONS, start=1):
            if version <= current:
                continue
            await db.executescript(script)
            await db.execute(f"PRAGMA user_version={version}")
            log.info("migration_applied", version=version)
        await db.commit()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteStateBackend.setup() no se ha llamado")
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def has_uid(self, uid: str) -> bool:
        db = self._require_db()
        async with db.execute("SELECT 1 FROM published WHERE uid = ?", (uid,)) as cursor:
            return await cursor.fetchone() is not None

    async def has_sha256(self, digest: str) -> bool:
        db = self._require_db()
        async with db.execute("SELECT 1 FROM published WHERE sha256 = ?", (digest,)) as cursor:
            return await cursor.fetchone() is not None

    async def known_phashes(self, *, within_days: int = 90) -> list[tuple[str, str]]:
        # Se limita la ventana porque comparar contra todo el historico crece
        # sin limite y un repost de hace un ano ya no molesta a nadie.
        cutoff = (utcnow() - timedelta(days=within_days)).isoformat()
        db = self._require_db()
        async with db.execute(
            "SELECT uid, phash FROM published WHERE phash IS NOT NULL AND published_at >= ?",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [(str(row["uid"]), str(row["phash"])) for row in rows]

    async def record(self, item: PublishedItem) -> None:
        db = self._require_db()
        await db.execute(
            """
            INSERT INTO published (
                uid, source, source_id, permalink, sha256, phash, score, kind,
                author, telegram_message_id, telegram_file_id, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                telegram_message_id = excluded.telegram_message_id,
                telegram_file_id    = excluded.telegram_file_id,
                published_at        = excluded.published_at
            """,
            (
                item.uid,
                item.source,
                item.source_id,
                item.permalink,
                item.sha256,
                item.phash,
                item.score,
                str(item.kind),
                item.author,
                item.telegram_message_id,
                item.telegram_file_id,
                item.published_at.isoformat(),
            ),
        )
        await db.commit()

    async def find(self, source: str, source_id: str) -> PublishedItem | None:
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM published WHERE uid = ?", (f"{source}:{source_id}",)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return PublishedItem(
            source=str(row["source"]),
            source_id=str(row["source_id"]),
            permalink=str(row["permalink"]),
            sha256=str(row["sha256"]),
            phash=row["phash"],
            score=float(row["score"] or 0),
            author=str(row["author"] or ""),
            telegram_message_id=row["telegram_message_id"],
            telegram_file_id=row["telegram_file_id"],
        )

    async def record_feedback(self, item: PublishedItem, kind: str) -> None:
        db = self._require_db()
        await db.execute(
            "INSERT INTO feedback (uid, source, author, kind, created_at) VALUES (?, ?, ?, ?, ?)",
            (item.uid, item.source, item.author, kind, utcnow().isoformat()),
        )
        await db.commit()

    async def disliked_authors(self) -> dict[str, int]:
        db = self._require_db()
        async with db.execute(
            "SELECT author, COUNT(*) AS n FROM feedback "
            "WHERE kind = 'dislike' AND author != '' GROUP BY author"
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["author"]): int(row["n"]) for row in rows}

    async def stats(self, *, since: datetime | None = None) -> dict[str, int]:
        db = self._require_db()
        if since is None:
            query, params = "SELECT source, COUNT(*) AS n FROM published GROUP BY source", ()
        else:
            query = (
                "SELECT source, COUNT(*) AS n FROM published "
                "WHERE published_at >= ? GROUP BY source"
            )
            params = (since.isoformat(),)  # type: ignore[assignment]
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return {str(row["source"]): int(row["n"]) for row in rows}

    async def total_published(self) -> int:
        db = self._require_db()
        async with db.execute("SELECT COUNT(*) FROM published") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


def build_state_backend(settings: Settings) -> StateBackendProtocol:
    """Construye el backend elegido en `SCRAPPY_STATE_BACKEND`."""
    match settings.state_backend:
        case StateBackend.SQLITE:
            return SqliteStateBackend(settings.state_db_path)
        case StateBackend.MEMORY:
            return MemoryStateBackend()
        case StateBackend.NONE:
            return NullStateBackend()
