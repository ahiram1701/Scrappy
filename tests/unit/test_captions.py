"""Pruebas de los captions.

Lo importante aqui es que la atribucion nunca se pierda y que el titulo, que
viene de internet, no pueda romper el formato del mensaje.
"""

from __future__ import annotations

from scrappy.config.loader import DeliveryConfig
from scrappy.delivery.captions import CAPTION_LIMIT, build_caption
from tests.conftest import make_candidate, make_scored


def test_incluye_autor_y_enlace_al_original() -> None:
    scored = make_scored(make_candidate(author="pepita", title="Mi meme"))
    caption = build_caption(scored, DeliveryConfig())

    assert "pepita" in caption
    assert scored.candidate.permalink in caption
    assert "ver original" in caption


def test_escapa_el_html_del_titulo() -> None:
    """Un titulo con HTML no debe poder inyectar etiquetas en el mensaje."""
    scored = make_scored(make_candidate(title='<a href="http://malo.test">pincha aqui</a> & mas'))
    caption = build_caption(scored, DeliveryConfig())

    assert "&lt;a href=" in caption
    assert "&amp;" in caption
    # Con `<` escapado ninguna etiqueta llega a formarse: el unico `<a` vivo
    # del caption es el nuestro, el de la atribucion.
    assert caption.count("<a ") == 2  # autor y "ver original"


def test_una_url_con_comillas_no_se_escapa_del_atributo() -> None:
    """Las URLs las da la plataforma de origen: son entrada no confiable.

    Sin escapar las comillas, un `author_url` como el de abajo cerraria el
    atributo `href` y podria colar los suyos.
    """
    candidate = make_candidate().model_copy(
        update={"author_url": 'https://ok.test/" onmouseover="robar()'}
    )
    caption = build_caption(make_scored(candidate), DeliveryConfig())

    # Las comillas quedan como entidades, asi que el atributo nunca se cierra:
    # `onmouseover` acaba siendo texto dentro del href, no un atributo nuevo.
    assert "&quot;" in caption
    assert '" onmouseover="' not in caption


def test_escapa_el_html_del_autor() -> None:
    scored = make_scored(make_candidate(author="<b>troll</b>"))
    caption = build_caption(scored, DeliveryConfig())
    assert "&lt;b&gt;troll&lt;/b&gt;" in caption


def test_recorta_los_titulos_largos_sin_perder_la_atribucion() -> None:
    scored = make_scored(make_candidate(title="palabra " * 400))
    caption = build_caption(scored, DeliveryConfig())

    assert len(caption) <= CAPTION_LIMIT
    assert "ver original" in caption
    assert scored.candidate.permalink in caption


def test_sin_titulo_queda_solo_la_atribucion() -> None:
    scored = make_scored(make_candidate(title=""))
    caption = build_caption(scored, DeliveryConfig())

    assert caption.startswith(("👽", "por"))
    assert "ver original" in caption


def test_la_insignia_de_fuente_es_opcional() -> None:
    scored = make_scored(make_candidate(source="reddit"))

    con = build_caption(scored, DeliveryConfig(show_source_badge=True))
    sin = build_caption(scored, DeliveryConfig(show_source_badge=False))

    assert "👽" in con
    assert "👽" not in sin
    # La atribucion sigue estando en los dos casos.
    assert "ver original" in sin


def test_el_score_solo_aparece_si_se_pide() -> None:
    scored = make_scored(score=0.876)

    assert "0.876" in build_caption(scored, DeliveryConfig(show_score=True))
    assert "0.876" not in build_caption(scored, DeliveryConfig(show_score=False))


def test_autor_sin_url_sigue_acreditado() -> None:
    candidate = make_candidate(author="anonimo").model_copy(update={"author_url": None})
    caption = build_caption(make_scored(candidate), DeliveryConfig())

    assert "por anonimo" in caption
    assert "ver original" in caption
