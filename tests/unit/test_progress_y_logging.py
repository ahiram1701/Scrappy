"""Pruebas de los dos ganchos que la TUI necesita del nucleo.

Ambos son aditivos: sin usarlos, el pipeline y el logging se comportan
exactamente igual que antes. Eso es justo lo que se verifica aqui.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings
from scrappy.core.pipeline import Pipeline, ProgressEvent
from scrappy.observability.logging import configure_logging, get_logger
from scrappy.storage.backends import MemoryStateBackend
from scrappy.storage.dedup import Deduplicator
from tests.conftest import FakeAdapter, FakePublisher, make_candidate
from tests.integration.test_pipeline import FakeDownloader


def _pipeline(
    settings: Settings,
    sources_config: SourcesConfig,
    state: MemoryStateBackend,
    *,
    adapters: list[object] | None = None,
    publisher: object | None = None,
) -> Pipeline:
    return Pipeline(
        settings=settings,
        sources_config=sources_config,
        adapters=adapters  # type: ignore[arg-type]
        or [FakeAdapter("reddit", [make_candidate(source_id=f"c{i}") for i in range(3)])],
        downloader=FakeDownloader(),
        deduplicator=Deduplicator(state),
        state=state,
        publisher=publisher or FakePublisher(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# on_progress
# ---------------------------------------------------------------------------
async def test_sin_callback_el_pipeline_funciona_igual(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """El gancho es opcional: la CLI y el bot no lo pasan."""
    report = await _pipeline(settings, sources_config, state).run(limit=1)
    assert report.finished_at is not None


async def test_avisa_de_cada_etapa(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    eventos: list[ProgressEvent] = []

    await _pipeline(settings, sources_config, state).run(limit=2, on_progress=eventos.append)

    etapas = [evento.stage for evento in eventos]
    assert etapas[0] == "discovering"
    assert etapas[-1] == "finished"
    assert "filtering" in etapas
    assert "ranking" in etapas
    assert "publishing" in etapas


async def test_la_etapa_de_publicacion_lleva_progreso_contado(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """`current`/`total` permiten pintar una barra en vez de un indeterminado."""
    eventos: list[ProgressEvent] = []

    await _pipeline(settings, sources_config, state).run(limit=2, on_progress=eventos.append)

    publicaciones = [e for e in eventos if e.stage == "publishing"]
    assert publicaciones
    assert publicaciones[0].current == 1
    assert all(e.total == 2 for e in publicaciones)


async def test_el_dry_run_no_llega_a_publishing(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    eventos: list[ProgressEvent] = []

    await _pipeline(settings, sources_config, state).run(dry_run=True, on_progress=eventos.append)

    assert "publishing" not in [e.stage for e in eventos]
    assert eventos[-1].stage == "finished"


async def test_un_callback_que_falla_no_tumba_el_run(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """Manda el pipeline, no la interfaz que escucha."""

    def _revienta(_evento: ProgressEvent) -> None:
        raise RuntimeError("la interfaz peto")

    report = await _pipeline(settings, sources_config, state).run(limit=1, on_progress=_revienta)

    assert report.finished_at is not None
    assert report.published


async def test_sin_candidatos_tambien_avisa_del_final(
    settings: Settings, sources_config: SourcesConfig, state: MemoryStateBackend
) -> None:
    """Sin este aviso la TUI se quedaria con el spinner girando para siempre."""
    eventos: list[ProgressEvent] = []

    await _pipeline(settings, sources_config, state, adapters=[FakeAdapter("reddit", [])]).run(
        limit=1, on_progress=eventos.append
    )

    assert eventos[-1].stage == "finished"


# ---------------------------------------------------------------------------
# log_file
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _restaurar_logging() -> object:
    """Deja el logging como estaba: `force=True` pisa la config global."""
    yield
    configure_logging("INFO", "console")


def test_con_log_file_no_se_escribe_en_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Es la razon de ser del parametro: Textual es dueno del terminal."""
    destino = tmp_path / "logs" / "tui.log"
    configure_logging("INFO", "console", log_file=destino)

    get_logger("prueba").info("evento_de_prueba", dato=42)
    logging.shutdown()

    assert destino.exists()
    assert "evento_de_prueba" in destino.read_text(encoding="utf-8")
    assert "evento_de_prueba" not in capsys.readouterr().out


def test_el_fichero_de_log_no_lleva_escapes_ansi(tmp_path: Path) -> None:
    """stdout sigue siendo un tty aunque el handler apunte a disco."""
    destino = tmp_path / "tui.log"
    configure_logging("INFO", "console", log_file=destino)

    get_logger("prueba").warning("aviso", fuente="reddit")
    logging.shutdown()

    assert "\x1b[" not in destino.read_text(encoding="utf-8")


def test_crea_el_directorio_si_no_existe(tmp_path: Path) -> None:
    destino = tmp_path / "sin" / "crear" / "tui.log"
    configure_logging("INFO", "console", log_file=destino)

    get_logger("prueba").info("hola")
    logging.shutdown()

    assert destino.exists()


def test_sin_log_file_sigue_yendo_a_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """El comportamiento por defecto no cambia."""
    configure_logging("INFO", "console")

    get_logger("prueba").info("evento_en_consola")

    assert "evento_en_consola" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Sin consola: el autoarranque
# ---------------------------------------------------------------------------
def test_sin_consola_no_revienta_y_deja_los_logs_en_fichero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El fallo que dejaba el autoarranque muerto en el arranque.

    La tarea de inicio de sesion lanza `pythonw.exe`, que no tiene consola: ahi
    `sys.stdout` es None y preguntarle `isatty()` levantaba un `AttributeError`
    antes de que arrancara nada. Scrappy moria al segundo de iniciar sesion,
    sin ventana donde verlo y sin una linea de log en ningun sitio.
    """
    destino = tmp_path / "scrappy.log"
    monkeypatch.setattr("scrappy.observability.logging.LOG_SIN_CONSOLA", destino)
    monkeypatch.setattr(sys, "stdout", None)

    configure_logging("INFO", "console")
    get_logger("prueba").info("arranque_sin_consola")
    logging.shutdown()

    assert destino.exists()
    assert "arranque_sin_consola" in destino.read_text(encoding="utf-8")


def test_el_log_en_fichero_rota_y_no_se_come_el_disco(tmp_path: Path) -> None:
    """El proceso del autoarranque no termina nunca: sin rotar, crece sin fin."""
    from logging.handlers import RotatingFileHandler

    configure_logging("INFO", "console", log_file=tmp_path / "scrappy.log")

    manejadores = logging.getLogger().handlers
    assert any(isinstance(m, RotatingFileHandler) for m in manejadores)


def test_los_secretos_se_siguen_redactando_en_fichero(tmp_path: Path) -> None:
    """La redaccion no puede perderse por cambiar de destino."""
    destino = tmp_path / "tui.log"
    configure_logging("INFO", "console", log_file=destino)

    get_logger("prueba").info("arranque", token="secreto-que-no-debe-salir")
    logging.shutdown()

    contenido = destino.read_text(encoding="utf-8")
    assert "secreto-que-no-debe-salir" not in contenido
    assert "***" in contenido
