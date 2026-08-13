"""Los cambios del catalogo tienen que notarse sin reiniciar.

El boton «Vetar autor» responde «No volvera a aparecer», y no era verdad: el
filtro se construia una sola vez, al crear el pipeline, asi que el veto se
escribia en el fichero, se veia en `/config` y el autor seguia publicandose
hasta el siguiente reinicio. Un boton que promete algo que no cumple es peor
que no tenerlo.

La asimetria que lo delataba: el scorer SI se rehacia en cada ronda para
releer los votos del 👎, con su comentario explicando por que. La misma
pregunta no se le habia hecho al filtro.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.app import ScrappyApp
from scrappy.config.loader import load_sources_config
from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import utcnow
from scrappy.tui.yaml_editor import SourcesYamlEditor
from tests.conftest import make_candidate

CATALOGO = """\
ranking:
  weights: {engagement: 0.5, velocity: 0.35, source: 0.15}
  penalties: {too_long: 0.2, too_short: 0.1, no_thumbnail: 0.05, low_comment_ratio: 0.1}
  min_comment_ratio: 0.002
  min_score: 0.30
sources:
  reddit:
    weight: 0.9
    budget: 10
    subreddits: [memes]
filters:
  blocked_keywords: []
  blocked_authors: []
  allowed_languages: []
delivery:
  show_score: false
  show_source_badge: true
  silent_notifications: false
  delay_between_posts: 4
"""


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    ruta = tmp_path / "sources.yaml"
    ruta.write_text(CATALOGO, encoding="utf-8")
    return ruta


@pytest.fixture
def settings(tmp_path: Path, yaml_path: Path) -> Settings:
    return Settings(
        state_backend=StateBackend.MEMORY,
        workspace_root=tmp_path / "ws",
        sources_config_path=yaml_path,
        max_age_hours=48,
    )


async def _app(settings: Settings) -> ScrappyApp:
    return await ScrappyApp.create(settings, with_publisher=False)


def _vetar(yaml_path: Path, autor: str) -> None:
    """Lo que hace el boton, tal cual."""
    editor = SourcesYamlEditor(yaml_path)
    editor.load()
    vetados = list(editor.get_value(["filters", "blocked_authors"], []) or [])
    vetados.append(autor)
    editor.set_value(["filters", "blocked_authors"], vetados)
    editor.save()


# ---------------------------------------------------------------------------
# El veto
# ---------------------------------------------------------------------------
async def test_vetar_surte_efecto_sin_reiniciar(settings: Settings, yaml_path: Path) -> None:
    """El fallo, en una prueba: el filtro se quedaba con el catalogo viejo."""
    app = await _app(settings)
    try:
        candidato = make_candidate(author="Molesto")
        assert app.pipeline._filter.evaluate(candidato, now=utcnow()).accepted

        _vetar(yaml_path, "Molesto")
        app.sources_config = load_sources_config(yaml_path)

        veredicto = app.pipeline._filter.evaluate(candidato, now=utcnow())
        assert not veredicto.accepted
        assert "vetado" in veredicto.reason
    finally:
        await app.aclose()


async def test_la_aplicacion_y_el_pipeline_no_pueden_discrepar(settings: Settings) -> None:
    """Eran dos copias distintas, y ese era el fondo del asunto.

    El boton actualizaba la de `ScrappyApp` y el pipeline seguia con la suya,
    asi que el veto se veia en `/config` sin afectar a lo que se publicaba.
    """
    app = await _app(settings)
    try:
        assert app.sources_config is app.pipeline.sources_config

        nuevo = app.sources_config.model_copy(deep=True)
        object.__setattr__(nuevo.filters, "blocked_authors", ["alguien"])
        app.sources_config = nuevo

        assert app.pipeline.sources_config is nuevo
        assert "alguien" in app.pipeline._filter._config.blocked_authors
    finally:
        await app.aclose()


# ---------------------------------------------------------------------------
# Editar el fichero a mano
# ---------------------------------------------------------------------------
async def test_el_catalogo_se_relee_en_cada_ronda(settings: Settings, yaml_path: Path) -> None:
    """Es lo que la documentacion promete de `sources.yaml`."""
    app = await _app(settings)
    try:
        _vetar(yaml_path, "EditadoAMano")
        # Sin avisar a nadie: como quien edita el fichero con el bot en marcha.
        await app.run_pipeline(dry_run=True)

        assert "editadoamano" in app.sources_config.filters.blocked_authors
    finally:
        await app.aclose()


async def test_un_catalogo_roto_no_para_las_publicaciones(
    settings: Settings, yaml_path: Path
) -> None:
    """Un YAML a medio editar no puede tumbar el bot.

    Se sigue con el ultimo valido, que lo es por definicion: se cargo bien en
    su momento.
    """
    app = await _app(settings)
    try:
        anterior = app.sources_config
        yaml_path.write_text("esto: no es: un yaml valido: [", encoding="utf-8")

        report = await app.run_pipeline(dry_run=True)

        assert report is not None
        assert app.sources_config is anterior
    finally:
        await app.aclose()


async def test_un_cambio_de_peso_se_aplica_en_la_siguiente_ronda(
    settings: Settings, yaml_path: Path
) -> None:
    app = await _app(settings)
    try:
        assert app.sources_config.ranking.min_score == 0.30

        yaml_path.write_text(
            CATALOGO.replace("min_score: 0.30", "min_score: 0.70"), encoding="utf-8"
        )
        await app.run_pipeline(dry_run=True)

        assert app.sources_config.ranking.min_score == 0.70
    finally:
        await app.aclose()


async def test_sin_cambios_no_se_reconstruye_nada(settings: Settings) -> None:
    """Releer un fichero que no cambio no debe tirar el filtro cada ronda."""
    app = await _app(settings)
    try:
        filtro = app.pipeline._filter
        await app.run_pipeline(dry_run=True)
        assert app.pipeline._filter is filtro
    finally:
        await app.aclose()
