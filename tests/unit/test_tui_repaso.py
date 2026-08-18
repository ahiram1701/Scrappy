"""Repaso sistematico de la interfaz.

`test_tui.py` prueba comportamientos concretos. Esto es lo complementario: un
recorrido que toca cada pantalla, cada boton y cada campo, buscando lo que se
escapa cuando cada cosa se prueba por separado -un widget que se queda sin
refrescar, un boton que apunta a un id que ya no existe, un campo declarado
que no llega a pintarse-.

Es el tipo de fallo que tuvo la configuracion: cada pieza funcionaba, y el
conjunto apagaba fuentes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select, Static, Switch, TabbedContent

from scrappy.tui.fields import (
    CONTENT_FIELDS,
    SCHEDULE_FIELDS,
    SOURCE_ENABLED_KEY,
    STORAGE_FIELDS,
    TELEGRAM_FIELDS,
    EnvField,
    FieldKind,
)
from scrappy.tui.main import ScrappyTUI
from scrappy.tui.screens.candidates import CandidatesScreen
from scrappy.tui.screens.dashboard import DashboardScreen
from scrappy.tui.screens.help import HelpScreen
from scrappy.tui.screens.settings import SettingsScreen
from tests.unit.test_tui import _texto

ENV = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_STATE_BACKEND=memory
SCRAPPY_TIMEZONE=America/Mexico_City
"""


@pytest.fixture
def entorno(tmp_path: Path) -> Path:
    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"{ENV}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n", encoding="utf-8"
    )
    return env_path


# ---------------------------------------------------------------------------
# Que todo lo declarado llegue a pintarse
# ---------------------------------------------------------------------------
_ENV_DECLARADOS: tuple[EnvField, ...] = (
    *TELEGRAM_FIELDS,
    *CONTENT_FIELDS,
    *SCHEDULE_FIELDS,
    *STORAGE_FIELDS,
)


async def test_cada_campo_declarado_tiene_su_widget(entorno: Path) -> None:
    """Un campo en `fields.py` que no se pinta es un ajuste inalcanzable."""
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        for campo in _ENV_DECLARADOS:
            assert pilot.app.screen.query(f"#{campo.widget_id}"), f"falta {campo.key}"


async def test_cada_fuente_tiene_su_pestana_y_su_interruptor(entorno: Path) -> None:
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        for fuente, clave in SOURCE_ENABLED_KEY.items():
            assert pilot.app.screen.query(f"#tab-src-{fuente}"), f"falta la pestana de {fuente}"
            assert pilot.app.screen.query(f"#env-{clave}"), f"falta el interruptor de {fuente}"


async def test_una_fuente_sin_seccion_en_el_yaml_sigue_saliendo(tmp_path: Path) -> None:
    """Un `sources.yaml` viejo dejaba fuentes sin pestana, sin decir nada.

    Pasaba con los ficheros creados antes de que existiera un adapter: la
    pantalla listaba las secciones del YAML, asi que esa fuente no se podia
    ni activar ni configurar, y nada explicaba por que faltaba.
    """
    from textual.widgets import Switch

    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    # Un YAML sin imgur ni giphy, como el que tenia el usuario.
    texto = ejemplo.read_text(encoding="utf-8")
    recortado = re.sub(r"\n  imgur:.*?\n  youtube:", "\n  youtube:", texto, flags=re.DOTALL)
    recortado = re.sub(r"\n  giphy:.*?\n  youtube:", "\n  youtube:", recortado, flags=re.DOTALL)
    assert "imgur:" not in recortado and "giphy:" not in recortado

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(recortado, encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"{ENV}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n", encoding="utf-8"
    )

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        pantalla = pilot.app.screen

        for fuente in ("imgur", "giphy"):
            assert pantalla.query(f"#tab-src-{fuente}"), f"falta la pestana de {fuente}"
            # El interruptor si funciona: vive en el `.env`, no en el YAML.
            assert pantalla.query_one(f"#env-SCRAPPY_{fuente.upper()}_ENABLED", Switch)

        # Y se explica que falta, en vez de dejar campos que no guardarian nada.
        avisos = " ".join(_texto(w) for w in pantalla.query("Static"))
        assert "no tiene seccion" in avisos
        assert "sources.example.yaml" in avisos

        # Sin seccion no se pintan sus campos: el editor no crea claves, asi
        # que serian widgets que no hacen nada.
        assert not pantalla.query("#yaml-sources__imgur__weight")


async def test_el_tipo_de_widget_corresponde_al_tipo_del_campo(entorno: Path) -> None:
    """Un booleano en una caja de texto se puede rellenar con cualquier cosa."""
    esperado = {
        FieldKind.BOOL: Switch,
        FieldKind.CHOICE: Select,
        FieldKind.NUMBER: Input,
        FieldKind.TEXT: Input,
    }

    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        for campo in _ENV_DECLARADOS:
            widget = pilot.app.screen.query_one(f"#{campo.widget_id}")
            assert isinstance(widget, esperado[campo.kind]), campo.key


async def test_los_secretos_salen_enmascarados(entorno: Path) -> None:
    """El token no debe verse en pantalla mientras no se pida."""
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        token = pilot.app.screen.query_one("#env-SCRAPPY_TELEGRAM_BOT_TOKEN", Input)
        assert token.password

        pilot.app.screen.query_one("#revelar-secretos", Switch).value = True
        await pilot.pause()
        assert not token.password


async def test_cada_campo_lleva_su_ayuda_o_se_explica_solo(entorno: Path) -> None:
    """La ayuda es lo unico que dice *por que* tocar un valor."""
    sin_ayuda = [c.key for c in _ENV_DECLARADOS if not c.help and c.kind is FieldKind.TEXT]
    # Los de texto sin explicacion son los sospechosos: un numero o un
    # interruptor con etiqueta clara puede pasar sin ayuda, una cadena no.
    assert sin_ayuda == [], f"campos de texto sin explicar: {sin_ayuda}"


# ---------------------------------------------------------------------------
# Que los botones hagan lo que dicen
# ---------------------------------------------------------------------------
async def test_ningun_boton_apunta_a_un_id_inexistente(entorno: Path) -> None:
    """Un boton cuyo manejador no lo reconoce es un boton que no hace nada.

    Se pulsan todos los del panel y se comprueba que la pantalla sigue viva:
    lo que se busca no es el efecto de cada uno -eso se prueba aparte- sino
    que ninguno reviente ni se quede sin manejador.
    """
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        for boton in list(pilot.app.screen.query(Button)):
            if boton.disabled or boton.id in {
                "autoarranque-on",
                "autoarranque-sistema",
                "autoarranque-off",
            }:
                # Los de autoarranque abren un modal y tocan el sistema; se
                # prueban con un doble en test_autostart.py
                continue
            boton.press()
            await pilot.pause()

        assert isinstance(pilot.app.screen, DashboardScreen)


async def test_sin_telegram_no_se_puede_publicar(tmp_path: Path) -> None:
    """El boton tiene que estar deshabilitado, no fallar al pulsarlo."""
    env_path = tmp_path / ".env"
    env_path.write_text("SCRAPPY_STATE_BACKEND=memory\n", encoding="utf-8")

    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert not tui.can_publish

        await pilot.press("c")
        await pilot.pause()
        assert pilot.app.screen.query_one("#publicar", Button).disabled


# ---------------------------------------------------------------------------
# Que los estados vacios expliquen algo
# ---------------------------------------------------------------------------
async def test_candidatos_sin_explorar_dice_que_hacer(entorno: Path) -> None:
    """Una pantalla en blanco no le dice a nadie por donde empezar."""
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()

        texto = _texto(pilot.app.screen.query_one("#barra-progreso", Static))
        assert "Explorar" in texto
        # Y que tranquilice: explorar no publica nada.
        assert "No descarga nada" in texto


async def test_el_desglose_vacio_explica_para_que_sirve(entorno: Path) -> None:
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()

        texto = _texto(pilot.app.screen.query_one("#desglose-texto", Static))
        assert "Selecciona" in texto


async def test_el_panel_dice_donde_esta_la_configuracion(entorno: Path) -> None:
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        texto = _texto(pilot.app.screen.query_one("#estado-config", Static))
        assert str(entorno) in texto
        # Y la zona horaria resuelta, que es de donde salen las horas.
        assert "America/Mexico_City" in texto


# ---------------------------------------------------------------------------
# Que refrescar no rompa nada, en ninguna pantalla
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tecla", ["d", "s"])
async def test_refrescar_no_rompe_la_pantalla(entorno: Path, tecla: str) -> None:
    """Candidatos queda fuera: refrescar alli sale a la red."""
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press(tecla)
        await pilot.pause()
        pantalla = type(pilot.app.screen)

        await pilot.press("r")
        await pilot.pause()

        assert type(pilot.app.screen) is pantalla


@pytest.mark.parametrize("tecla", ["d", "c", "s"])
async def test_recargar_no_rompe_ninguna_pantalla(entorno: Path, tecla: str) -> None:
    """La recarga en caliente reconstruye la aplicacion bajo los pies de la UI."""
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        await pilot.press(tecla)
        await pilot.pause()
        pantalla = type(pilot.app.screen)

        assert await tui.recargar() is True
        await pilot.pause()

        assert type(pilot.app.screen) is pantalla
        assert tui.scrappy is not None


async def test_recargar_en_candidatos_no_sale_a_la_red(entorno: Path) -> None:
    """Recargar la configuracion no es lo mismo que querer buscar contenido.

    Si `sincronizar()` llamara a `refresh_data()`, recargar dispararia el
    pipeline entero contra media docena de plataformas sin que nadie lo pida.
    """
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        await pilot.press("c")
        await pilot.pause()

        pantalla: CandidatesScreen = pilot.app.screen  # type: ignore[assignment]
        await tui.recargar()
        await pilot.pause()

        # Ni exploracion en marcha ni resultados aparecidos de la nada.
        assert not pantalla._ocupado
        assert pantalla._filas == []


# ---------------------------------------------------------------------------
# Los modales
# ---------------------------------------------------------------------------
async def test_la_ayuda_se_abre_y_se_cierra(entorno: Path) -> None:
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HelpScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(pilot.app.screen, DashboardScreen)


async def test_la_ayuda_cubre_todos_los_atajos_globales(entorno: Path) -> None:
    """Una ayuda incompleta es peor que no tenerla: se confia en ella."""
    from scrappy.tui.screens.help import _AYUDA

    for binding in ScrappyTUI.BINDINGS:
        tecla = binding.key  # type: ignore[union-attr]
        if tecla == "question_mark":
            continue
        assert f"  {tecla} " in _AYUDA, f"la ayuda no menciona «{tecla}»"


async def test_la_ayuda_responde_cuando_corre_scrappy(entorno: Path) -> None:
    """Es la duda que trae a la gente aqui: por que dejo de publicar."""
    from scrappy.tui.screens.help import _AYUDA

    assert "Solo mientras haya un proceso suyo vivo" in _AYUDA
    assert "arranque automatico" in _AYUDA


# ---------------------------------------------------------------------------
# Que se salga limpiamente
# ---------------------------------------------------------------------------
async def test_al_salir_se_cierra_todo(entorno: Path) -> None:
    """El apagado tiene que purgar los workspaces y cerrar las conexiones."""
    tui = ScrappyTUI(env_path=entorno, show_wizard=False)
    async with tui.run_test():
        scrappy = tui.scrappy
        assert scrappy is not None

    assert scrappy.client.is_closed


@pytest.mark.parametrize(
    "pantalla", [DashboardScreen, CandidatesScreen, SettingsScreen], ids=lambda p: p.__name__
)
async def test_salir_desde_cualquier_pantalla(entorno: Path, pantalla: type) -> None:
    tui = ScrappyTUI(env_path=entorno, show_wizard=False)
    async with tui.run_test() as pilot:
        await pilot.press(
            {"DashboardScreen": "d", "CandidatesScreen": "c"}.get(pantalla.__name__, "s")
        )
        await pilot.pause()

    assert tui.scrappy is not None
    assert tui.scrappy.client.is_closed


# ---------------------------------------------------------------------------
# Las pestanas del YAML
# ---------------------------------------------------------------------------
async def test_las_pestanas_de_configuracion_estan_todas(entorno: Path) -> None:
    async with ScrappyTUI(env_path=entorno, show_wizard=False).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        tabs = pilot.app.screen.query_one("#config-tabs", TabbedContent)
        # Por el id, que es estable; la etiqueta visible depende de la API de
        # Textual y ya cambio de nombre una vez.
        ids = {pane.id for pane in tabs.query("TabPane")}

        for titulo in ("telegram", "contenido", "programacion", "almacenamiento"):
            assert f"tab-{titulo}" in ids, f"falta la pestana {titulo}"
        for titulo in ("ranking", "filtros", "publicacion"):
            assert f"tab-{titulo}" in ids, f"falta la pestana {titulo}"
