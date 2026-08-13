"""La documentacion tiene que seguir describiendo el codigo que hay.

Una guia que se queda atras es peor que no tenerla: se confia en ella. Estas
pruebas no juzgan la redaccion, solo comprueban lo verificable — que cada
comando y cada boton que existe esta explicado, que los enlaces internos
llevan a alguna parte, y que `.env.example` cubre todas las variables.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path("docs")
RAIZ = Path()


def _leer(ruta: Path) -> str:
    if not ruta.exists():  # pragma: no cover
        pytest.skip(f"no se encuentra {ruta}")
    return ruta.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Comandos y botones del bot
# ---------------------------------------------------------------------------
def _comandos_registrados() -> set[str]:
    """Los comandos que el bot da de alta de verdad."""
    fuente = _leer(Path("src/scrappy/bot/handlers.py"))
    return set(re.findall(r'CommandHandler\("(\w+)"', fuente))


def test_cada_comando_del_bot_esta_documentado() -> None:
    texto = _leer(DOCS / "TELEGRAM.md")
    faltan = [f"/{c}" for c in sorted(_comandos_registrados()) if f"/{c}" not in texto]
    assert faltan == [], f"comandos sin documentar en TELEGRAM.md: {faltan}"


def test_el_menu_nativo_coincide_con_los_comandos_que_existen() -> None:
    """Un comando en el menu que no exista da «comando desconocido» al pulsarlo."""
    fuente = _leer(Path("src/scrappy/bot/handlers.py"))
    menu = re.search(r"_MENU = \[(.*?)\]", fuente, re.DOTALL)
    assert menu is not None

    en_menu = set(re.findall(r'\("(\w+)",', menu.group(1)))
    assert en_menu <= _comandos_registrados(), (
        f"el menu ofrece comandos que no existen: {en_menu - _comandos_registrados()}"
    )


def test_cada_boton_de_publicacion_esta_documentado() -> None:
    """Son los botones con los que mas se interactua, y los menos evidentes."""
    texto = _leer(DOCS / "TELEGRAM.md")
    etiquetas = re.findall(
        r'InlineKeyboardButton\("([^"]+)"', _leer(Path("src/scrappy/delivery/keyboards.py"))
    )
    assert etiquetas, "no se encontro ningun boton; cambio el formato del teclado?"

    for etiqueta in etiquetas:
        assert etiqueta in texto, f"el boton «{etiqueta}» no se explica en TELEGRAM.md"


def test_se_explica_si_cada_boton_se_puede_deshacer() -> None:
    """Lo que mas falta hace saber antes de pulsar algo permanente."""
    texto = _leer(DOCS / "TELEGRAM.md")
    assert "¿Se puede deshacer?" in texto
    # El veto es el unico permanente que se escribe en un fichero.
    assert "blocked_authors" in texto


# ---------------------------------------------------------------------------
# Variables de configuracion
# ---------------------------------------------------------------------------
def test_el_ejemplo_cubre_todas_las_variables() -> None:
    """Una variable que no este en `.env.example` no la descubre nadie."""
    from scrappy.config.settings import Settings

    ejemplo = _leer(Path(".env.example"))
    faltan = [
        f"SCRAPPY_{nombre.upper()}"
        for nombre in Settings.model_fields
        if f"SCRAPPY_{nombre.upper()}" not in ejemplo
    ]
    assert faltan == [], f"variables sin ejemplo: {faltan}"


def test_la_referencia_documenta_todas_las_variables() -> None:
    """La referencia omite el prefijo en sus tablas y lo dice en la cabecera.

    Se busca `` `NOMBRE` `` entre comillas invertidas, que es como aparece en
    la columna «Variable»: buscar el nombre suelto daria falsos positivos con
    cualquier mencion en un parrafo.
    """
    from scrappy.config.settings import Settings

    referencia = _leer(DOCS / "CONFIGURATION.md")
    faltan = [
        nombre.upper()
        for nombre in Settings.model_fields
        if f"`{nombre.upper()}`" not in referencia
    ]
    assert faltan == [], f"variables sin documentar en CONFIGURATION.md: {faltan}"


# ---------------------------------------------------------------------------
# Enlaces
# ---------------------------------------------------------------------------
def _documentos() -> list[Path]:
    return [*DOCS.rglob("*.md"), Path("README.md")]


@pytest.mark.parametrize("documento", _documentos(), ids=lambda p: str(p))
def test_los_enlaces_internos_llevan_a_alguna_parte(documento: Path) -> None:
    """Un enlace roto en la puerta de entrada es lo peor que puede pasar."""
    texto = _leer(documento)
    rotos: list[str] = []

    for destino in re.findall(r"\]\(([^)]+)\)", texto):
        if destino.startswith(("http://", "https://", "#", "mailto:")):
            continue
        ruta = (documento.parent / destino.split("#")[0]).resolve()
        if not ruta.exists():
            rotos.append(destino)

    assert rotos == [], f"enlaces rotos en {documento}: {rotos}"
