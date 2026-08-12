"""Pruebas de la TUI con el `Pilot` de Textual.

Corren sin terminal real y sin red: los ajustes apuntan a un `sources.yaml`
inexistente, asi que ninguna fuente tiene nada configurado y `discover()`
devuelve vacio sin salir a internet.

La prueba que mas importa es `test_cancelar_el_modal_no_publica`: publicar es
irreversible desde la interfaz, y la confirmacion es lo unico que separa un
pulsado accidental de un mensaje en el canal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import RunReport
from scrappy.core.pipeline import DryRunRow
from scrappy.sources.registry import iter_adapter_names
from scrappy.tui.main import ScrappyTUI, StatusBar
from scrappy.tui.screens.candidates import CandidatesScreen
from scrappy.tui.screens.dashboard import DashboardScreen
from scrappy.tui.screens.settings import SettingsScreen
from scrappy.tui.widgets.confirm import ConfirmModal
from tests.conftest import make_candidate

if TYPE_CHECKING:
    from textual.widgets import Static


def _texto(widget: Static) -> str:
    """Texto de un `Static`.

    Textual 8 retiro `Static.renderable`, asi que se pasa por `render()`. Vive
    aqui para que un cambio de API de Textual se arregle en un solo sitio.
    """
    return str(widget.render())


@pytest.fixture
def tui_settings(tmp_path: Path) -> Settings:
    """Ajustes sin credenciales y sin fuentes: arranque rapido y sin red."""
    return Settings(
        telegram_bot_token="",  # type: ignore[arg-type]
        telegram_target_chat_id="",
        state_backend=StateBackend.MEMORY,
        workspace_root=tmp_path / "ws",
        sources_config_path=tmp_path / "no-existe.yaml",
    )


# ---------------------------------------------------------------------------
# Arranque y navegacion
# ---------------------------------------------------------------------------
async def test_arranca_y_muestra_el_panel(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        assert isinstance(pilot.app.screen, DashboardScreen)
        assert pilot.app.scrappy is not None  # type: ignore[attr-defined]


async def test_navega_entre_las_tres_pantallas(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        await pilot.press("c")
        assert isinstance(pilot.app.screen, CandidatesScreen)

        await pilot.press("s")
        assert isinstance(pilot.app.screen, SettingsScreen)

        await pilot.press("d")
        assert isinstance(pilot.app.screen, DashboardScreen)


async def test_no_apila_pantallas_al_navegar(tui_settings: Settings) -> None:
    """Sin esto, cambiar de pantalla 20 veces dejaria 20 pantallas vivas."""
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        for _ in range(5):
            await pilot.press("c")
            await pilot.press("d")
        # La base mas la actual.
        assert len(pilot.app.screen_stack) == 2


async def test_el_panel_pinta_la_salud_y_las_fuentes(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        from textual.widgets import DataTable, Static

        assert "ffmpeg" in _texto(pilot.app.screen.query_one("#salud", Static))

        tabla = pilot.app.screen.query_one("#tabla-fuentes", DataTable)
        # Todas las fuentes conocidas aparecen, aunque esten desactivadas: si
        # una falta, lo que hay que revisar es por que no se construyo.
        # Se cuenta contra el registro y no contra un numero escrito a mano,
        # que se desincroniza en cuanto se anade una fuente.
        assert tabla.row_count == len(list(iter_adapter_names()))


# ---------------------------------------------------------------------------
# Degradacion sin credenciales
# ---------------------------------------------------------------------------
async def test_sin_telegram_no_se_puede_publicar(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        assert pilot.app.can_publish is False  # type: ignore[attr-defined]

        await pilot.press("c")
        from textual.widgets import Button

        assert pilot.app.screen.query_one("#publicar", Button).disabled


async def test_lo_dice_en_la_barra_de_estado(tui_settings: Settings) -> None:
    """Mejor explicarlo que dejar un boton gris sin motivo."""
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        barra = pilot.app.query_one("#status-bar", StatusBar)
        assert "solo lectura" in barra.message


# ---------------------------------------------------------------------------
# Candidatos
# ---------------------------------------------------------------------------
class _PipelineFalso:
    """Sustituye al pipeline real para no depender de la red en los tests."""

    def __init__(self) -> None:
        self.last_dry_run = [
            DryRunRow(make_candidate(source_id="a", engagement=9000), 0.91, "SELECCIONADO"),
            DryRunRow(make_candidate(source_id="b", engagement=120), 0.22, "nota baja"),
        ]
        self.runs: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> RunReport:
        self.runs.append(kwargs)
        report = RunReport(dry_run=bool(kwargs.get("dry_run")))
        report.discovered = 2
        return report


async def test_explorar_llena_la_tabla(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        pilot.app.scrappy.pipeline = _PipelineFalso()  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("e")
        await pilot.pause()

        from textual.widgets import DataTable

        tabla = pilot.app.screen.query_one("#tabla-candidatos", DataTable)
        assert tabla.row_count == 2


async def test_el_desglose_explica_la_fila_seleccionada(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        pilot.app.scrappy.pipeline = _PipelineFalso()  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("e")
        await pilot.pause()

        from textual.widgets import DataTable, Static

        pilot.app.screen.query_one("#tabla-candidatos", DataTable).focus()
        await pilot.pause()

        desglose = _texto(pilot.app.screen.query_one("#desglose-texto", Static))
        assert "score" in desglose
        assert "engagement" in desglose


# ---------------------------------------------------------------------------
# LA prueba: la confirmacion protege de publicar sin querer
# ---------------------------------------------------------------------------
async def test_publicar_pide_confirmacion(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        pilot.app.can_publish = True  # type: ignore[attr-defined]
        pilot.app.scrappy.pipeline = _PipelineFalso()  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("p")
        await pilot.pause()

        assert isinstance(pilot.app.screen, ConfirmModal)


async def test_cancelar_el_modal_no_publica(tui_settings: Settings) -> None:
    """Publicar es irreversible: cancelar tiene que dejarlo todo como estaba."""
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        falso = _PipelineFalso()
        pilot.app.can_publish = True  # type: ignore[attr-defined]
        pilot.app.scrappy.pipeline = falso  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        # Ni una sola llamada al pipeline: no se ejecuto ningun run.
        assert falso.runs == []
        barra = pilot.app.query_one("#status-bar", StatusBar)
        assert "cancelada" in barra.message


async def test_el_foco_arranca_en_cancelar(tui_settings: Settings) -> None:
    """Pulsar Enter sin leer el modal no debe publicar."""
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        pilot.app.can_publish = True  # type: ignore[attr-defined]
        pilot.app.scrappy.pipeline = _PipelineFalso()  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("p")
        await pilot.pause()

        assert pilot.app.focused is not None
        assert pilot.app.focused.id == "cancelar"


async def test_confirmar_si_publica(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        falso = _PipelineFalso()
        pilot.app.can_publish = True  # type: ignore[attr-defined]
        pilot.app.scrappy.pipeline = falso  # type: ignore[attr-defined,assignment]

        await pilot.press("c")
        await pilot.press("p")
        await pilot.pause()
        await pilot.click("#confirmar")
        await pilot.pause()

        assert len(falso.runs) == 1
        # Y no en seco: esta vez publica de verdad.
        assert not falso.runs[0].get("dry_run")


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
async def test_la_configuracion_avisa_si_falta_el_fichero(tui_settings: Settings) -> None:
    async with ScrappyTUI(tui_settings).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Static

        aviso = _texto(pilot.app.screen.query_one("#config-aviso", Static))
        assert "sources.example.yaml" in aviso


@pytest.fixture
def entorno_completo(tmp_path: Path) -> tuple[Settings, Path]:
    """Copias reales de los dos ficheros de configuracion, en un temporal."""
    ejemplo_yaml = Path("config/sources.example.yaml")
    ejemplo_env = Path(".env.example")
    if not ejemplo_yaml.exists() or not ejemplo_env.exists():  # pragma: no cover
        pytest.skip("no se encuentran las plantillas del proyecto")

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(ejemplo_env.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(
        telegram_bot_token="",  # type: ignore[arg-type]
        telegram_target_chat_id="",
        state_backend=StateBackend.MEMORY,
        workspace_root=tmp_path / "ws",
        sources_config_path=yaml_path,
    )
    return settings, env_path


async def test_la_configuracion_cubre_el_env_y_el_yaml(
    entorno_completo: tuple[Settings, Path],
) -> None:
    """La queja original era que apenas habia nada configurable."""
    settings, env_path = entorno_completo

    async with ScrappyTUI(settings, env_path=env_path).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Input, Select, Switch

        ids = {w.id for w in pilot.app.screen.query(Input)}
        ids |= {w.id for w in pilot.app.screen.query(Switch)}
        ids |= {w.id for w in pilot.app.screen.query(Select)}

        # Del .env, que antes no se tocaba en absoluto.
        assert "env-SCRAPPY_TELEGRAM_BOT_TOKEN" in ids
        assert "env-SCRAPPY_ITEMS_PER_RUN" in ids
        assert "env-SCRAPPY_STATE_BACKEND" in ids
        assert "env-SCRAPPY_ALLOW_NSFW" in ids
        # Secciones del YAML que antes faltaban.
        assert "yaml-ranking__penalties__too_long" in ids
        assert "yaml-filters__blocked_authors" in ids
        assert "yaml-delivery__show_score" in ids
        # Ajustes propios de cada fuente.
        assert "yaml-sources__bluesky__window_hours" in ids
        assert "yaml-sources__reddit__subreddits_per_run" in ids


async def test_hay_interruptor_para_activar_cada_fuente(
    entorno_completo: tuple[Settings, Path],
) -> None:
    settings, env_path = entorno_completo

    async with ScrappyTUI(settings, env_path=env_path).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Switch

        ids = {w.id for w in pilot.app.screen.query(Switch)}
        assert "env-SCRAPPY_REDDIT_ENABLED" in ids
        assert "env-SCRAPPY_TIKTOK_ENABLED" in ids


async def test_el_token_se_muestra_enmascarado(
    entorno_completo: tuple[Settings, Path],
) -> None:
    """Un token visible en pantalla es un token que se filtra en una captura."""
    settings, env_path = entorno_completo

    async with ScrappyTUI(settings, env_path=env_path).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Input

        token = pilot.app.screen.query_one("#env-SCRAPPY_TELEGRAM_BOT_TOKEN", Input)
        assert token.password is True

        # Y el interruptor lo revela cuando hace falta comprobarlo.
        await pilot.click("#revelar-secretos")
        await pilot.pause()
        assert token.password is False


async def test_guardar_conserva_los_comentarios_de_ambos_ficheros(
    entorno_completo: tuple[Settings, Path],
) -> None:
    settings, env_path = entorno_completo

    async with ScrappyTUI(settings, env_path=env_path).run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        from textual.widgets import Input

        campo = pilot.app.screen.query_one("#yaml-ranking__min_score", Input)
        campo.value = "0.42"
        await pilot.press("ctrl+s")
        await pilot.pause()

    yaml_guardado = settings.sources_config_path.read_text(encoding="utf-8")
    assert "min_score: 0.42" in yaml_guardado
    assert "OJO CON EL RATE LIMIT" in yaml_guardado
    assert "OJO CON EL SIGNO" in env_path.read_text(encoding="utf-8")
