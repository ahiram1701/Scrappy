"""Pruebas de los filtros duros.

Un rechazo aqui ahorra una descarga entera, asi que importa tanto que rechacen
lo que deben como que no rechacen de mas.
"""

from __future__ import annotations

import pytest

from scrappy.config.loader import FiltersConfig, SourcesConfig
from scrappy.config.settings import Settings
from scrappy.ranking.filters import CandidateFilter
from tests.conftest import FIXED_NOW, make_candidate


@pytest.fixture
def candidate_filter(settings: Settings, sources_config: SourcesConfig) -> CandidateFilter:
    return CandidateFilter(settings, sources_config.filters)


def test_acepta_un_candidato_normal(candidate_filter: CandidateFilter) -> None:
    verdict = candidate_filter.evaluate(make_candidate(), now=FIXED_NOW)
    assert verdict.accepted


def test_rechaza_nsfw_por_defecto(candidate_filter: CandidateFilter) -> None:
    verdict = candidate_filter.evaluate(make_candidate(nsfw=True), now=FIXED_NOW)
    assert not verdict.accepted
    assert verdict.reason == "nsfw"


def test_acepta_nsfw_si_se_permite(settings: Settings, sources_config: SourcesConfig) -> None:
    permisivo = settings.model_copy(update={"allow_nsfw": True})
    verdict = CandidateFilter(permisivo, sources_config.filters).evaluate(
        make_candidate(nsfw=True), now=FIXED_NOW
    )
    assert verdict.accepted


def test_rechaza_lo_demasiado_antiguo(candidate_filter: CandidateFilter) -> None:
    verdict = candidate_filter.evaluate(make_candidate(age_hours=200), now=FIXED_NOW)
    assert not verdict.accepted
    assert "antiguo" in verdict.reason


def test_rechaza_por_duracion(candidate_filter: CandidateFilter) -> None:
    largo = candidate_filter.evaluate(make_candidate(duration=600), now=FIXED_NOW)
    corto = candidate_filter.evaluate(make_candidate(duration=0.2), now=FIXED_NOW)
    assert not largo.accepted and "largo" in largo.reason
    assert not corto.accepted and "corto" in corto.reason


def test_no_rechaza_cuando_se_desconoce_la_duracion(
    candidate_filter: CandidateFilter,
) -> None:
    """Muchas fuentes no informan de la duracion; no es motivo para descartar."""
    verdict = candidate_filter.evaluate(make_candidate(duration=None), now=FIXED_NOW)
    assert verdict.accepted


def test_rechaza_palabras_vetadas(candidate_filter: CandidateFilter) -> None:
    verdict = candidate_filter.evaluate(
        make_candidate(title="Advertencia: GORE explicito"), now=FIXED_NOW
    )
    assert not verdict.accepted
    assert "gore" in verdict.reason


def test_rechaza_autores_vetados(candidate_filter: CandidateFilter) -> None:
    verdict = candidate_filter.evaluate(make_candidate(author="Spammer"), now=FIXED_NOW)
    assert not verdict.accepted
    assert "autor vetado" in verdict.reason


def test_filtro_de_idioma(settings: Settings) -> None:
    config = FiltersConfig(allowed_languages=["es", "en"])
    filtro = CandidateFilter(settings, config)

    assert filtro.evaluate(make_candidate(language="es"), now=FIXED_NOW).accepted
    assert not filtro.evaluate(make_candidate(language="de"), now=FIXED_NOW).accepted
    # Sin idioma declarado no se filtra: mejor un falso positivo que perderselo.
    assert filtro.evaluate(make_candidate(language=None), now=FIXED_NOW).accepted


def test_partition_conserva_los_motivos(candidate_filter: CandidateFilter) -> None:
    candidatos = [
        make_candidate(source_id="ok"),
        make_candidate(source_id="viejo", age_hours=500),
        make_candidate(source_id="nsfw", nsfw=True),
    ]

    aceptados, rechazados = candidate_filter.partition(candidatos, now=FIXED_NOW)

    assert [c.source_id for c in aceptados] == ["ok"]
    assert {c.source_id: motivo for c, motivo in rechazados}.keys() == {"viejo", "nsfw"}
