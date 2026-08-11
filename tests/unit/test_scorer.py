"""Pruebas del ranking.

Lo que hay que verificar aqui no es "el numero sale bien", sino las propiedades
de las que depende que el bot publique lo que debe:

  1. Que las metricas NO se comparen en bruto entre plataformas.
  2. Que un post joven y en explosion gane a uno viejo y ya saturado.
  3. Que las penalizaciones se apliquen y sean legibles en el desglose.
"""

from __future__ import annotations

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings
from scrappy.ranking.scorer import Scorer, _percentile_map, _percentile_value, _velocity
from tests.conftest import FIXED_NOW, make_candidate


def test_percentil_reparte_el_rango_completo() -> None:
    mapa = _percentile_map([10, 20, 30])
    assert mapa[10] == 0.0
    assert mapa[30] == 1.0
    assert 0.0 < mapa[20] < 1.0


def test_percentil_con_un_solo_valor_es_el_mejor() -> None:
    """Si una fuente solo trajo un item, ese item es lo mejor que trajo."""
    assert _percentile_map([7]) == {7: 1.0}


def test_percentil_agrupa_empates() -> None:
    mapa = _percentile_map([5, 5, 5, 9])
    assert mapa[5] == 0.0
    assert mapa[9] == 1.0


def test_percentile_value_interpola() -> None:
    assert _percentile_value([0.0, 10.0], 0.5) == 5.0
    assert _percentile_value([], 0.9) == 0.0
    assert _percentile_value([3.0], 0.9) == 3.0


def test_velocidad_premia_lo_reciente() -> None:
    joven = make_candidate(engagement=5_000, age_hours=2)
    viejo = make_candidate(engagement=5_000, age_hours=40)
    assert _velocity(joven, FIXED_NOW) > _velocity(viejo, FIXED_NOW)


def test_las_escalas_de_cada_plataforma_no_se_mezclan(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    """El nucleo del diseno: 900k reproducciones de TikTok no aplastan a Reddit.

    Se le da a TikTok numeros dos ordenes de magnitud mayores. Si el ranking
    comparase engagement bruto, los tres primeros puestos serian de TikTok.
    Como compara percentiles, el mejor de Reddit tiene que colarse arriba.
    """
    candidates = [
        make_candidate(source="reddit", source_id=f"r{i}", engagement=eng, age_hours=3)
        for i, eng in enumerate([1_000, 5_000, 30_000])
    ] + [
        make_candidate(source="tiktok", source_id=f"t{i}", engagement=eng, age_hours=3)
        for i, eng in enumerate([100_000, 500_000, 900_000])
    ]

    ranked = Scorer(settings, sources_config).rank(candidates, now=FIXED_NOW)
    top_3 = {item.candidate.source for item in ranked[:3]}

    assert "reddit" in top_3, "el mejor de Reddit deberia competir con el de TikTok"

    # Y el peor de TikTok no puede ganar al mejor de Reddit solo por escala.
    mejor_reddit = next(i for i in ranked if i.candidate.source == "reddit")
    peor_tiktok = next(i for i in reversed(ranked) if i.candidate.source == "tiktok")
    assert mejor_reddit.score > peor_tiktok.score


def test_penaliza_los_clips_demasiado_largos(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    largo = make_candidate(source_id="largo", duration=600)
    normal = make_candidate(source_id="normal", duration=20)

    ranked = Scorer(settings, sources_config).rank([largo, normal], now=FIXED_NOW)
    por_id = {item.candidate.source_id: item for item in ranked}

    assert "too_long" in por_id["largo"].breakdown.penalties
    assert "too_long" not in por_id["normal"].breakdown.penalties


def test_penaliza_la_falta_de_miniatura(settings: Settings, sources_config: SourcesConfig) -> None:
    sin_thumb = make_candidate(source_id="sin", thumbnail_url=None)
    ranked = Scorer(settings, sources_config).rank([sin_thumb], now=FIXED_NOW)
    assert "no_thumbnail" in ranked[0].breakdown.penalties


def test_penaliza_engagement_sin_conversacion(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    """Muchos likes y ningun comentario huele a inflado."""
    sospechoso = make_candidate(source_id="raro", engagement=50_000, comments=1)
    ranked = Scorer(settings, sources_config).rank([sospechoso], now=FIXED_NOW)
    assert "low_comment_ratio" in ranked[0].breakdown.penalties


def test_no_penaliza_baja_conversacion_en_posts_pequenos(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    """Con 50 likes, cero comentarios no significa nada."""
    pequeno = make_candidate(source_id="pequeno", engagement=50, comments=0)
    ranked = Scorer(settings, sources_config).rank([pequeno], now=FIXED_NOW)
    assert "low_comment_ratio" not in ranked[0].breakdown.penalties


def test_select_corta_por_nota_y_por_limite(
    settings: Settings, sources_config: SourcesConfig
) -> None:
    candidates = [
        make_candidate(source_id=f"c{i}", engagement=eng, age_hours=2)
        for i, eng in enumerate([100, 1_000, 10_000, 50_000, 200_000])
    ]

    elegidos, descartados = Scorer(settings, sources_config).select(
        candidates, limit=2, now=FIXED_NOW
    )

    assert len(elegidos) <= 2
    assert all(item.score >= sources_config.ranking.min_score for item in elegidos)
    assert all(item.score < sources_config.ranking.min_score for item in descartados)


def test_el_score_nunca_es_negativo(settings: Settings, sources_config: SourcesConfig) -> None:
    """Con todas las penalizaciones encima, el score se queda en cero, no baja."""
    horrible = make_candidate(
        source_id="horrible",
        engagement=0,
        comments=0,
        duration=9999,
        thumbnail_url=None,
        age_hours=47,
    )
    ranked = Scorer(settings, sources_config).rank([horrible], now=FIXED_NOW)
    assert ranked[0].score >= 0.0


def test_lote_vacio(settings: Settings, sources_config: SourcesConfig) -> None:
    assert Scorer(settings, sources_config).rank([]) == []
