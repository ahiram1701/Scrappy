"""El comando `/status`.

Existe porque saber como estaba Scrappy obligaba a encadenar `/start`,
`/health`, `/sources` y `/stats`. Lo que se prueba aqui es que el resumen
sigue diciendo la verdad en los casos degradados, que son justo cuando se
consulta: recien reiniciado, sin rondas todavia, o con la ultima fallida.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scrappy.app import ScrappyApp
from scrappy.bot.handlers import texto_status
from scrappy.config.settings import Settings
from scrappy.core.models import UltimaRonda


class SchedulerFalso:
    def __init__(self, *, running: bool = True, proxima: str | None = "hoy a las 21:20") -> None:
        self.running = running
        self.enabled = True
        self.next_run_at = proxima


@pytest.fixture
async def app(tmp_path: Path) -> Any:
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text("sources:\n  reddit:\n    subreddits:\n      - memes\n", encoding="utf-8")
    settings = Settings(
        telegram_bot_token="123456789:TOKEN-DE-PRUEBA-NO-REAL",
        telegram_target_chat_id="1412545148",
        state_backend="memory",
        sources_config_path=yaml_path,
    )
    scrappy = await ScrappyApp.create(settings, with_publisher=False)
    try:
        yield scrappy
    finally:
        await scrappy.aclose()


async def test_dice_desde_cuando_esta_en_marcha(app: ScrappyApp) -> None:
    """Es la primera pregunta despues de reiniciar el equipo.

    Con el arranque automatico no hay ventana que mirar, asi que esta linea es
    la unica forma de saber si el proceso sobrevivio.
    """
    texto = await texto_status(app, SchedulerFalso())
    assert "en marcha desde" in texto


async def test_sin_rondas_todavia_lo_dice_en_vez_de_callarse(app: ScrappyApp) -> None:
    """Una linea ausente se lee como «algo va mal»; una que lo explica, no."""
    texto = await texto_status(app, SchedulerFalso())
    assert "aun no ha corrido ninguna" in texto


async def test_ensena_el_resultado_de_la_ultima_ronda(app: ScrappyApp) -> None:
    app.ultima_ronda = UltimaRonda(resumen="run descubiertos=47 published=3 en 22.4s")
    texto = await texto_status(app, SchedulerFalso())
    assert "published=3" in texto


async def test_una_ronda_fallida_se_marca(app: ScrappyApp) -> None:
    """Ensenar el ultimo exito mientras todo revienta seria peor que callarse."""
    app.ultima_ronda = UltimaRonda(resumen="fallo: no hay red", correcta=False)
    texto = await texto_status(app, SchedulerFalso())

    assert "⚠️" in texto
    assert "no hay red" in texto


async def test_sin_scheduler_no_promete_una_hora(app: ScrappyApp) -> None:
    """La misma regla que `/start`: no se da una hora que no se puede consultar."""
    texto = await texto_status(app, None)
    assert "no hay rondas programadas" in texto


async def test_los_motivos_de_las_fuentes_van_recortados(app: ScrappyApp) -> None:
    """El detalle de imgur trae la URL donde sacar la clave y el limite del
    plan gratuito. Eso esta muy bien en `/sources` y aqui hace un muro."""
    texto = await texto_status(app, SchedulerFalso())
    for linea in texto.splitlines():
        if linea.startswith("Por revisar"):
            assert "http" not in linea
            assert len(linea) < 400


async def test_el_texto_es_html_valido(app: ScrappyApp) -> None:
    """Se manda con `parse_mode=HTML`: una etiqueta mal cerrada y Telegram
    rechaza el mensaje entero."""
    from xml.etree import ElementTree

    app.ultima_ronda = UltimaRonda(resumen="run <con> etiquetas & simbolos")
    texto = await texto_status(app, SchedulerFalso())
    ElementTree.fromstring(f"<raiz>{texto}</raiz>")
