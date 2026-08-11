"""Pruebas de integracion del pipeline completo.

Ejercitan las seis etapas con dobles de prueba en los extremos (fuentes falsas,
publisher falso, descarga simulada) y sin red.

La prueba mas importante de todo el proyecto esta aqui:
`test_no_queda_ningun_fichero_tras_un_run` y su gemela para el caso de fallo.
Si alguna de las dos se rompe, la promesa central —el contenido no se queda en
la maquina— ha dejado de cumplirse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import DownloadError, SourceError
from scrappy.core.models import (
    EphemeralMedia,
    MediaKind,
    RawCandidate,
    RunOutcome,
)
from scrappy.core.pipeline import Pipeline
from scrappy.storage.backends import MemoryStateBackend
from scrappy.storage.dedup import Deduplicator
from tests.conftest import FakeAdapter, FakePublisher, make_candidate

pytestmark = pytest.mark.integration


class FakeDownloader:
    """Descarga simulada que escribe un fichero real en el workspace.

    Escribir de verdad es lo que hace util la prueba: si el pipeline se dejara
    algo sin borrar, el fichero seguiria ahi al terminar.
    """

    def __init__(self, *, fail_for: set[str] | None = None, size: int = 2048) -> None:
        self.fail_for = fail_for or set()
        self.size = size
        self.written: list[Path] = []

    async def fetch(self, candidate: RawCandidate, workspace: Path) -> EphemeralMedia:
        if candidate.source_id in self.fail_for:
            raise DownloadError(f"fallo simulado con {candidate.uid}")

        path = workspace / f"{candidate.source_id}.mp4"
        # Contenido distinto por item para que los sha256 no colisionen.
        path.write_bytes(candidate.source_id.encode().ljust(self.size, b"\0"))
        self.written.append(path)

        return EphemeralMedia(
            candidate=candidate,
            kind=MediaKind.VIDEO,
            filename=path.name,
            path=path,
            size_bytes=path.stat().st_size,
            duration_seconds=candidate.duration_seconds,
        )


def _build_pipeline(
    settings: Settings,
    sources_config: SourcesConfig,
    state: MemoryStateBackend,
    *,
    adapters: list[Any],
    downloader: Any,
    publisher: Any,
) -> Pipeline:
    return Pipeline(
        settings=settings,
        sources_config=sources_config,
        adapters=adapters,
        downloader=downloader,
        deduplicator=Deduplicator(state, phash_threshold=settings.phash_threshold),
        state=state,
        publisher=publisher,
    )


def _workspace_files(root: Path) -> list[Path]:
    """Todo fichero que quede bajo la raiz de workspaces."""
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------
async def test_publica_los_mejores_candidatos(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    candidates = [
        make_candidate(source_id=f"c{i}", engagement=eng, age_hours=2)
        for i, eng in enumerate([500, 5_000, 80_000, 200_000])
    ]
    publisher = FakePublisher()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[FakeAdapter("reddit", candidates)],
        downloader=FakeDownloader(),
        publisher=publisher,
    )

    report = await pipeline.run(limit=2)

    assert report.outcomes.get(RunOutcome.PUBLISHED) == 2
    assert len(publisher.published) == 2
    # Se publica lo mejor, no los dos primeros que llegaron.
    publicados = {scored.candidate.source_id for scored, _ in publisher.published}
    assert publicados == {"c2", "c3"}


async def test_lo_publicado_queda_registrado(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[FakeAdapter("reddit", [make_candidate(source_id="unico")])],
        downloader=FakeDownloader(),
        publisher=FakePublisher(),
    )

    await pipeline.run(limit=1)

    assert await state.has_uid("reddit:unico")
    assert await state.total_published() == 1


async def test_la_segunda_pasada_lo_descarta_por_duplicado(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """El caso real: el scheduler vuelve a encontrar el mismo post tres horas despues."""
    candidates = [make_candidate(source_id="repetido")]
    publisher = FakePublisher()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[FakeAdapter("reddit", candidates)],
        downloader=FakeDownloader(),
        publisher=publisher,
    )

    primera = await pipeline.run(limit=1)
    segunda = await pipeline.run(limit=1)

    assert primera.outcomes.get(RunOutcome.PUBLISHED) == 1
    assert segunda.outcomes.get(RunOutcome.DUPLICATE) == 1
    assert len(publisher.published) == 1


# ---------------------------------------------------------------------------
# LA garantia
# ---------------------------------------------------------------------------
async def test_no_queda_ningun_fichero_tras_un_run(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """Tras publicar cinco items, el disco tiene que estar exactamente igual."""
    root = settings.workspace_root
    assert root is not None

    downloader = FakeDownloader()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter(
                "reddit",
                [
                    make_candidate(source_id=f"item{i}", engagement=10_000 * (i + 1))
                    for i in range(5)
                ],
            )
        ],
        downloader=downloader,
        publisher=FakePublisher(),
    )

    report = await pipeline.run(limit=5)

    assert report.outcomes.get(RunOutcome.PUBLISHED) == 5
    # Se escribieron ficheros de verdad...
    assert len(downloader.written) == 5
    # ...y no queda ni uno.
    assert _workspace_files(root) == []
    assert all(not path.exists() for path in downloader.written)


async def test_no_queda_ningun_fichero_cuando_falla_la_publicacion(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """El caso peligroso: el medio ya esta en disco y el envio revienta."""
    root = settings.workspace_root
    assert root is not None

    downloader = FakeDownloader()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter(
                "reddit",
                [
                    make_candidate(source_id=f"item{i}", engagement=10_000 * (i + 1))
                    for i in range(3)
                ],
            )
        ],
        downloader=downloader,
        # Falla el segundo envio, con el fichero ya escrito en el workspace.
        publisher=FakePublisher(fail_on=2),
    )

    report = await pipeline.run(limit=3)

    assert report.outcomes.get(RunOutcome.PUBLISH_FAILED, 0) >= 1
    assert _workspace_files(root) == []
    assert all(not path.exists() for path in downloader.written)


async def test_no_queda_ningun_fichero_cuando_falla_la_descarga(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    root = settings.workspace_root
    assert root is not None

    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter(
                "reddit",
                [
                    make_candidate(source_id="roto", engagement=90_000),
                    make_candidate(source_id="bueno", engagement=50_000),
                ],
            )
        ],
        downloader=FakeDownloader(fail_for={"roto"}),
        publisher=FakePublisher(),
    )

    report = await pipeline.run(limit=2)

    assert report.outcomes.get(RunOutcome.DOWNLOAD_FAILED) == 1
    assert report.outcomes.get(RunOutcome.PUBLISHED) == 1
    assert _workspace_files(root) == []


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------
async def test_una_fuente_caida_no_impide_publicar_del_resto(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    publisher = FakePublisher()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter("tiktok", [], error=SourceError("tiktok", "la plataforma cambio")),
            FakeAdapter("reddit", [make_candidate(source_id="sano")]),
        ],
        downloader=FakeDownloader(),
        publisher=publisher,
    )

    report = await pipeline.run(limit=2)

    assert len(publisher.published) == 1
    assert any("tiktok" in error for error in report.errors)


async def test_los_filtros_se_aplican_antes_de_descargar(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    downloader = FakeDownloader()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter(
                "reddit",
                [
                    make_candidate(source_id="nsfw", nsfw=True, engagement=999_999),
                    make_candidate(source_id="viejo", age_hours=500, engagement=999_999),
                    make_candidate(source_id="ok", engagement=10_000),
                ],
            )
        ],
        downloader=downloader,
        publisher=FakePublisher(),
    )

    report = await pipeline.run(limit=3)

    assert report.outcomes.get(RunOutcome.FILTERED) == 2
    # Lo filtrado no llego a descargarse: es el ahorro que justifica el filtro.
    assert [p.stem for p in downloader.written] == ["ok"]


async def test_dry_run_no_toca_ni_disco_ni_telegram(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    root = settings.workspace_root
    assert root is not None

    downloader = FakeDownloader()
    publisher = FakePublisher()
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[
            FakeAdapter(
                "reddit",
                [make_candidate(source_id=f"c{i}", engagement=5_000 * (i + 1)) for i in range(4)],
            )
        ],
        downloader=downloader,
        publisher=publisher,
    )

    report = await pipeline.run(limit=2, dry_run=True)

    assert report.dry_run
    assert downloader.written == []
    assert publisher.published == []
    assert _workspace_files(root) == []
    # Pero si produce la explicacion que sirve para calibrar.
    assert pipeline.last_dry_run
    assert any(row.verdict == "SELECCIONADO" for row in pipeline.last_dry_run)


async def test_sin_candidatos_termina_limpio(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[FakeAdapter("reddit", [])],
        downloader=FakeDownloader(),
        publisher=FakePublisher(),
    )

    report = await pipeline.run(limit=5)

    assert report.discovered == 0
    assert report.finished_at is not None
    assert "sin resultados" in report.summary_line()


async def test_sin_fuentes_lo_dice_en_el_informe(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    pipeline = _build_pipeline(
        settings,
        sources_config,
        state,
        adapters=[],
        downloader=FakeDownloader(),
        publisher=FakePublisher(),
    )

    report = await pipeline.run(limit=1)

    assert any("fuente" in error for error in report.errors)
