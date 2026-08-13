"""Pruebas de la zona horaria y del formato de las horas.

El motivo: el scheduler estaba fijado a UTC, asi que el panel decia «proxima
ronda: 05:00» cuando en el reloj del usuario eran las 23:00.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scrappy.config.settings import Settings
from scrappy.core.tiempo import formato_local, formato_relativo

MEXICO = ZoneInfo("America/Mexico_City")


# ---------------------------------------------------------------------------
# Resolucion de la zona
# ---------------------------------------------------------------------------
def test_la_zona_configurada_manda() -> None:
    assert Settings(timezone="America/Mexico_City").tzinfo == MEXICO


def test_sin_zona_configurada_se_detecta_la_del_sistema() -> None:
    """Vacio no es UTC: es «la de esta maquina», sea cual sea."""
    zona = Settings(timezone="").tzinfo
    assert zona is not None
    # No se puede afirmar cual es -depende de donde corra el test- pero si que
    # sirve para convertir una fecha sin reventar.
    assert datetime.now(UTC).astimezone(zona).tzinfo is not None


def test_una_zona_que_no_existe_no_impide_publicar() -> None:
    """Una errata no debe tumbar el bot: se avisa y se sigue con la del sistema."""
    assert Settings(timezone="Mexico/CiudadDeMexico").tzinfo is not None


@pytest.mark.parametrize("valor", ["America/Mexico_City", "Europe/Madrid", "UTC"])
def test_zonas_iana_habituales(valor: str) -> None:
    assert str(Settings(timezone=valor).tzinfo) == valor


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------
def test_la_hora_se_muestra_en_local_no_en_utc() -> None:
    """El caso exacto que se veia mal: 05:00 UTC son las 23:00 del dia anterior."""
    momento = datetime(2026, 8, 13, 5, 0, tzinfo=UTC)
    assert "23:00" in formato_local(momento, MEXICO)
    assert "05:00" not in formato_local(momento, MEXICO)


def test_hoy_manana_y_ayer_se_dicen_asi() -> None:
    ahora = datetime.now(MEXICO)
    assert formato_local(ahora, MEXICO).startswith("hoy a las")
    assert formato_local(ahora + timedelta(days=1), MEXICO).startswith("manana a las")
    assert formato_local(ahora - timedelta(days=1), MEXICO).startswith("ayer a las")


def test_mas_alla_de_un_dia_lleva_fecha() -> None:
    dentro = datetime.now(MEXICO) + timedelta(days=5)
    texto = formato_local(dentro, MEXICO)
    assert dentro.strftime("%d/%m") in texto


def test_el_dia_se_decide_en_la_zona_local_no_en_utc() -> None:
    """Las 23:00 locales son «hoy», aunque en UTC ya sea el dia siguiente."""
    hoy_local = datetime.now(MEXICO).replace(hour=23, minute=0, second=0, microsecond=0)
    assert hoy_local.astimezone(UTC).date() != hoy_local.date()  # el caso limite
    assert formato_local(hoy_local, MEXICO).startswith("hoy a las")


@pytest.mark.parametrize(
    ("delta", "esperado"),
    [
        (timedelta(seconds=5), "ahora mismo"),
        (timedelta(minutes=25), "en 25 min"),
        (timedelta(hours=3), "en 3 h"),
        (timedelta(days=2), "en 2 d"),
        (timedelta(seconds=-5), "hace un momento"),
        (timedelta(minutes=-25), "hace 25 min"),
    ],
)
def test_duraciones_en_la_unidad_que_toca(delta: timedelta, esperado: str) -> None:
    assert formato_relativo(delta) == esperado
