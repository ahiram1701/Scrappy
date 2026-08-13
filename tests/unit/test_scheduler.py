"""Pruebas del scheduler.

Lo que importa aqui no es que APScheduler funcione -eso ya esta probado en
APScheduler- sino las tres decisiones propias: que dispare en la zona del
usuario, que un fallo del pipeline no desprograme el job, y que lo que se
muestra sea una hora local legible.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import pytest

from scrappy.config.settings import Settings
from scrappy.scheduler.jobs import PipelineScheduler

MEXICO = ZoneInfo("America/Mexico_City")


class AppFalsa:
    """Lo justo de `ScrappyApp` para el scheduler."""

    def __init__(self, settings: Settings, *, error: Exception | None = None) -> None:
        self.settings = settings
        self.paused = False
        self.runs = 0
        self._error = error

    async def run_pipeline(self) -> Any:
        self.runs += 1
        if self._error is not None:
            raise self._error

        class Report:
            @staticmethod
            def summary_line() -> str:
                return "1 publicado"

        return Report()


@pytest.fixture
def ajustes() -> Settings:
    return Settings(timezone="America/Mexico_City", schedule_interval_minutes=180)


def test_dispara_en_la_zona_del_usuario(ajustes: Settings) -> None:
    """Estaba fijado a UTC, y de ahi salia el desfase de seis horas."""
    scheduler = PipelineScheduler(AppFalsa(ajustes))  # type: ignore[arg-type]
    assert scheduler._scheduler.timezone == MEXICO


async def test_la_proxima_ronda_se_lee_en_local(ajustes: Settings) -> None:
    # Async porque `AsyncIOScheduler.start()` se engancha al bucle de eventos.
    scheduler = PipelineScheduler(AppFalsa(ajustes))  # type: ignore[arg-type]
    scheduler.start()
    try:
        proxima = scheduler.next_run_at
        assert proxima is not None
        # Ni ISO ni UTC: algo que se pueda leer en voz alta.
        assert "T" not in proxima
        assert "+00:00" not in proxima
        assert "a las" in proxima
    finally:
        scheduler.shutdown()


def test_parado_no_promete_ninguna_ronda(ajustes: Settings) -> None:
    assert PipelineScheduler(AppFalsa(ajustes)).next_run_at is None  # type: ignore[arg-type]


async def test_desactivado_ni_siquiera_arranca() -> None:
    ajustes = Settings(schedule_enabled=False)
    scheduler = PipelineScheduler(AppFalsa(ajustes))  # type: ignore[arg-type]

    # Y lo dice al devolver, en vez de no hacer nada en silencio: quien pulsa
    # «Arrancar» tiene que enterarse de por que no arranco.
    assert scheduler.start() is False
    assert not scheduler.running
    assert not scheduler.enabled


async def test_arrancar_dos_veces_no_revienta(ajustes: Settings) -> None:
    """`add_job` con un id que ya existe lanza `ConflictingIdError`.

    En la TUI eso era pulsar «Arrancar» dos veces seguidas.
    """
    scheduler = PipelineScheduler(AppFalsa(ajustes))  # type: ignore[arg-type]
    try:
        assert scheduler.start() is True
        assert scheduler.start() is True  # no debe levantar
        assert scheduler.running
    finally:
        scheduler.shutdown()


async def test_running_distingue_arrancado_de_desactivado(ajustes: Settings) -> None:
    """Son dos estados distintos, y mirarlos por `next_run_at` los mezclaba."""
    scheduler = PipelineScheduler(AppFalsa(ajustes))  # type: ignore[arg-type]

    # Habilitado en la configuracion, pero nadie lo ha arrancado.
    assert scheduler.enabled
    assert not scheduler.running

    try:
        scheduler.start()
        assert scheduler.enabled
        assert scheduler.running
    finally:
        scheduler.shutdown()


async def test_un_fallo_del_pipeline_no_desprograma_el_job(ajustes: Settings) -> None:
    """Si `_tick` propagara, APScheduler quitaria el job y el bot callaria."""
    app = AppFalsa(ajustes, error=RuntimeError("reventon simulado"))
    scheduler = PipelineScheduler(app)  # type: ignore[arg-type]

    await scheduler._tick()  # no debe levantar

    assert app.runs == 1


async def test_en_pausa_no_se_ejecuta(ajustes: Settings) -> None:
    app = AppFalsa(ajustes)
    app.paused = True
    await PipelineScheduler(app)._tick()  # type: ignore[arg-type]
    assert app.runs == 0
