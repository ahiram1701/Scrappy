"""Fixtures compartidas.

Ningun test toca la red ni Telegram: las llamadas HTTP se interceptan con
`respx` y el publisher se sustituye por un doble que guarda lo que "publicaria".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import (
    EphemeralMedia,
    MediaKind,
    PublishedItem,
    RawCandidate,
    ScoredCandidate,
)
from scrappy.storage.backends import MemoryStateBackend

FIXED_NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Ajustes aislados: estado en memoria y workspaces dentro de `tmp_path`."""
    return Settings(
        telegram_bot_token="123:TEST",  # type: ignore[arg-type]
        telegram_target_chat_id="-100999",
        telegram_admin_ids="42",
        state_backend=StateBackend.MEMORY,
        workspace_root=tmp_path / "ws",
        sources_config_path=tmp_path / "no-existe.yaml",
        max_download_mb=10,
        max_age_hours=48,
        min_duration_seconds=1,
        max_duration_seconds=180,
    )


@pytest.fixture
def sources_config() -> SourcesConfig:
    """Catalogo minimo con dos fuentes de pesos distintos."""
    return SourcesConfig.model_validate(
        {
            "ranking": {
                "weights": {"engagement": 0.5, "velocity": 0.35, "source": 0.15},
                "min_score": 0.30,
            },
            "sources": {
                "reddit": {"weight": 0.9, "budget": 10, "subreddits": ["memes"]},
                "tiktok": {"weight": 0.6, "budget": 10, "hashtags": ["humor"]},
            },
            "filters": {"blocked_keywords": ["gore"], "blocked_authors": ["spammer"]},
        }
    )


@pytest.fixture
def state() -> MemoryStateBackend:
    return MemoryStateBackend()


@pytest.fixture
async def http_client() -> Any:
    async with httpx.AsyncClient() as client:
        yield client


# ---------------------------------------------------------------------------
# Constructores de datos
# ---------------------------------------------------------------------------
def make_candidate(
    *,
    source: str = "reddit",
    source_id: str = "abc123",
    engagement: int = 5_000,
    comments: int = 200,
    age_hours: float = 4.0,
    duration: float | None = 20.0,
    title: str = "Un meme cualquiera",
    author: str = "alguien",
    nsfw: bool = False,
    kind: MediaKind = MediaKind.VIDEO,
    media_url: str | None = None,
    thumbnail_url: str | None = "https://example.test/thumb.jpg",
    language: str | None = None,
) -> RawCandidate:
    """Candidato de prueba con valores razonables por defecto."""
    return RawCandidate(
        source=source,
        source_id=source_id,
        permalink=f"https://example.test/{source}/{source_id}",
        title=title,
        author=author,
        author_url=f"https://example.test/u/{author}",
        kind=kind,
        media_url=media_url,
        thumbnail_url=thumbnail_url,
        duration_seconds=duration,
        created_at=FIXED_NOW - timedelta(hours=age_hours),
        engagement=engagement,
        comments=comments,
        nsfw=nsfw,
        language=language,
    )


def make_scored(candidate: RawCandidate | None = None, score: float = 0.8) -> ScoredCandidate:
    from scrappy.core.models import ScoreBreakdown

    return ScoredCandidate(
        candidate=candidate or make_candidate(),
        score=score,
        breakdown=ScoreBreakdown(),
    )


def make_media(
    candidate: RawCandidate | None = None,
    *,
    data: bytes = b"contenido-de-prueba",
    sha256: str = "a" * 64,
    phash: str | None = None,
) -> EphemeralMedia:
    candidate = candidate or make_candidate()
    return EphemeralMedia(
        candidate=candidate,
        kind=MediaKind.PHOTO,
        filename="prueba.jpg",
        data=data,
        size_bytes=len(data),
        sha256=sha256,
        phash=phash,
    )


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------
class FakePublisher:
    """Publisher que no habla con Telegram: solo apunta lo que le llega.

    Permite forzar un fallo en el enesimo envio, que es como se comprueba que
    el workspace se borra igualmente cuando la publicacion revienta.
    """

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.published: list[tuple[ScoredCandidate, EphemeralMedia]] = []
        self.texts: list[str] = []
        self._fail_on = fail_on
        self._calls = 0

    async def publish(self, scored: ScoredCandidate, media: EphemeralMedia) -> PublishedItem:
        self._calls += 1
        if self._fail_on is not None and self._calls == self._fail_on:
            from scrappy.core.errors import PublishError

            raise PublishError("fallo simulado de Telegram")

        # Se leen los bytes aqui a proposito: verifica que el medio sigue
        # existiendo en el momento de publicar y no antes de tiempo.
        payload = media.read_bytes()
        assert payload

        self.published.append((scored, media))
        return PublishedItem(
            source=scored.candidate.source,
            source_id=scored.candidate.source_id,
            permalink=scored.candidate.permalink,
            sha256=media.sha256,
            phash=media.phash,
            score=scored.score,
            kind=media.kind,
            telegram_message_id=1000 + self._calls,
            telegram_file_id=f"file-{self._calls}",
        )

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


class FakeAdapter:
    """Fuente de prueba que devuelve una lista fija de candidatos."""

    requires_tos_ack = False

    def __init__(
        self,
        name: str,
        candidates: list[RawCandidate],
        *,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self._candidates = candidates
        self._error = error

    async def status(self) -> Any:
        from scrappy.sources.base import SourceStatus

        return SourceStatus(name=self.name, enabled=True, configured=True)

    async def discover(self, budget: int) -> list[RawCandidate]:
        if self._error is not None:
            raise self._error
        return self._candidates[:budget]

    async def aclose(self) -> None:
        return None
