"""Pruebas de la UX del bot.

Sin red ni Telegram: se sustituyen el mensaje y la consulta por dobles que
guardan lo que se les manda. Lo que se verifica es el contenido, que es donde
esta el valor de estos cambios.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from scrappy.bot import callbacks, handlers
from scrappy.bot.keyboards import (
    ACCION_BORRAR,
    acciones_de_publicacion,
    menu_cantidad,
    menu_fetch,
    menu_stats,
)
from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import RunReport
from scrappy.sources.base import SourceStatus

TOKEN = "8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU"


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------
class FakeMessage:
    def __init__(self, chat_id: int = 1412545148) -> None:
        self.chat_id = chat_id
        self.enviados: list[str] = []
        self.editados: list[str] = []
        self.teclados: list[Any] = []

    async def reply_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.enviados.append(text)
        self.teclados.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.editados.append(text)


class FakeUser:
    def __init__(self, user_id: int = 1412545148) -> None:
        self.id = user_id


class FakeUpdate:
    def __init__(self, message: FakeMessage | None = None, user_id: int = 1412545148) -> None:
        self.effective_message = message or FakeMessage()
        self.effective_user = FakeUser(user_id)
        self.callback_query: FakeQuery | None = None


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.respuestas: list[tuple[str, bool]] = []
        self.editados: list[str] = []
        self.teclados: list[Any] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.respuestas.append((text, show_alert))

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.editados.append(text)
        self.teclados.append(kwargs.get("reply_markup"))


class FakeContext:
    def __init__(
        self,
        app: Any,
        admins: set[int] | None = None,
        args: list[str] | None = None,
        scheduler: Any = None,
    ):
        self.bot_data = {
            "scrappy_app": app,
            "admin_ids": frozenset(admins if admins is not None else {1412545148}),
            # None cuando quien arranco el bot no lo registro, que es lo que
            # pasa con `scrappy run --no-bot` y en la mayoria de estos tests.
            "scheduler": scheduler,
        }
        self.args = args or []


class FakeScheduler:
    """Las tres cosas que el bot pregunta del scheduler."""

    def __init__(self, *, running: bool = True, enabled: bool = True, proxima: str | None = None):
        self.running = running
        self.enabled = enabled
        self.next_run_at = proxima if proxima is not None else "hoy a las 19:52"


class FakeState:
    async def stats(self, since: Any = None) -> dict[str, int]:
        return {"reddit": 3, "lemmy": 2}

    async def total_published(self) -> int:
        return 42


class FakeApp:
    """Lo justo de `ScrappyApp` para estos handlers."""

    def __init__(self, settings: Settings, *, fuentes: list[SourceStatus] | None = None):
        self.settings = settings
        self.state = FakeState()
        self.adapters = [type("A", (), {"name": "reddit"})(), type("A", (), {"name": "lemmy"})()]
        self._fuentes = fuentes or [
            SourceStatus("reddit", True, True, "9 subreddits"),
            SourceStatus("tiktok", True, False, "falta el flag de ToS"),
        ]
        self.runs: list[dict[str, Any]] = []
        self.paused = False

    async def source_statuses(self) -> list[SourceStatus]:
        return self._fuentes

    async def run_pipeline(self, **kwargs: Any) -> RunReport:
        self.runs.append(kwargs)
        report = RunReport()
        report.discovered = 5
        return report

    async def health(self) -> Any:
        from scrappy.app import HealthReport

        return HealthReport(
            ffmpeg=True,
            state_backend="memory",
            state_items=42,
            telegram_configured=True,
            active_workspaces=0,
            sources=self._fuentes,
        )


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        telegram_bot_token=SecretStr(TOKEN),
        telegram_target_chat_id="1412545148",
        telegram_admin_ids="1412545148",
        state_backend=StateBackend.MEMORY,
        sources_config_path=tmp_path / "no.yaml",
    )


# ---------------------------------------------------------------------------
# /start: lo que pidio el usuario
# ---------------------------------------------------------------------------
async def test_start_confirma_que_este_es_el_chat_destino(settings: Settings) -> None:
    """Lo primero que uno quiere saber tras configurarlo: si le llega."""
    mensaje = FakeMessage(chat_id=1412545148)
    app = FakeApp(settings)

    await handlers.cmd_start(FakeUpdate(mensaje), FakeContext(app))  # type: ignore[arg-type]

    texto = mensaje.editados[0]
    assert "Este es el chat donde publicare" in texto


async def test_start_avisa_si_publicara_en_otro_sitio(settings: Settings) -> None:
    mensaje = FakeMessage(chat_id=999)
    app = FakeApp(settings)

    await handlers.cmd_start(FakeUpdate(mensaje), FakeContext(app))  # type: ignore[arg-type]

    assert "no aqui" in mensaje.editados[0]


async def test_start_lista_las_fuentes_listas_y_las_que_fallan(settings: Settings) -> None:
    mensaje = FakeMessage()
    app = FakeApp(settings)

    await handlers.cmd_start(FakeUpdate(mensaje), FakeContext(app))  # type: ignore[arg-type]

    texto = mensaje.editados[0]
    assert "Fuentes listas" in texto
    assert "reddit" in texto
    # Y explica que le pasa a la que esta activada pero incompleta.
    assert "falta el flag de ToS" in texto


async def test_start_dice_cada_cuanto_publicara(settings: Settings) -> None:
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler()),  # type: ignore[arg-type]
    )

    assert "cada" in mensaje.editados[0]
    assert "minutos" in mensaje.editados[0]


# ---------------------------------------------------------------------------
# Cuando es la proxima ronda
# ---------------------------------------------------------------------------
async def test_start_dice_cuando_es_la_proxima_ronda(settings: Settings) -> None:
    """Con el arranque automatico esto corre sin ventana: Telegram es el unico
    sitio donde se puede preguntar."""
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler(proxima="hoy a las 19:52")),  # type: ignore[arg-type]
    )

    assert "Proxima ronda" in mensaje.editados[0]
    assert "hoy a las 19:52" in mensaje.editados[0]


async def test_start_avisa_si_no_hay_rondas_programadas(settings: Settings) -> None:
    """Habilitado en la configuracion pero sin arrancar: hay que decirlo."""
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler(running=False)),  # type: ignore[arg-type]
    )

    assert "no hay rondas programadas" in mensaje.editados[0]


async def test_start_dice_si_esta_en_pausa(settings: Settings) -> None:
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))
    app.paused = True

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler()),  # type: ignore[arg-type]
    )

    texto = mensaje.editados[0]
    assert "En pausa" in texto
    assert "/resume" in texto
    # Y no promete una hora que no va a cumplir.
    assert "Proxima ronda" not in texto


async def test_sin_scheduler_registrado_no_se_inventa_una_hora(settings: Settings) -> None:
    """`scrappy run --no-bot` no registra ninguno, y los tests tampoco.

    Prometer una hora que no se puede consultar seria peor que no darla.
    """
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))

    await handlers.cmd_start(FakeUpdate(mensaje), FakeContext(app))  # type: ignore[arg-type]

    texto = mensaje.editados[0]
    assert "Proxima ronda" not in texto
    assert "cada" in texto  # la cadencia si se sabe


async def test_start_no_menciona_rondas_si_esta_desactivado(settings: Settings) -> None:
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": False}))

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler(enabled=False, running=False)),  # type: ignore[arg-type]
    )

    texto = mensaje.editados[0]
    assert "desactivado" in texto
    assert "Proxima ronda" not in texto


async def test_health_tambien_dice_la_proxima_ronda(settings: Settings) -> None:
    """El otro sitio natural para preguntarlo."""
    mensaje = FakeMessage()
    app = FakeApp(settings.model_copy(update={"schedule_enabled": True}))

    await handlers.cmd_health(
        FakeUpdate(mensaje),
        FakeContext(app, scheduler=FakeScheduler(proxima="manana a las 05:00")),  # type: ignore[arg-type]
    )

    assert "manana a las 05:00" in mensaje.enviados[0]


async def test_start_incluye_los_problemas_con_su_arreglo(tmp_path: Any) -> None:
    """El diagnostico se ve en el propio /start, no hay que ir a buscarlo."""
    roto = Settings(
        telegram_bot_token=SecretStr(TOKEN),
        telegram_target_chat_id="1412545148",
        telegram_admin_ids="",  # avisa
        reddit_user_agent="python-requests/2.0",  # avisa
        sources_config_path=tmp_path / "no.yaml",
    )
    mensaje = FakeMessage()

    await handlers.cmd_start(
        FakeUpdate(mensaje),
        FakeContext(FakeApp(roto), admins={1412545148}),  # type: ignore[arg-type]
    )

    texto = mensaje.editados[0]
    assert "Por revisar" in texto
    assert "429" in texto  # el aviso del User-Agent, con su explicacion


# ---------------------------------------------------------------------------
# Botones
# ---------------------------------------------------------------------------
async def test_fetch_sin_argumentos_ofrece_botones(settings: Settings) -> None:
    """Recordar `/fetch reddit 3` es justo lo que no deberia hacer falta."""
    mensaje = FakeMessage()

    await handlers.cmd_fetch(FakeUpdate(mensaje), FakeContext(FakeApp(settings)))  # type: ignore[arg-type]

    assert mensaje.teclados[0] is not None
    assert "De donde" in mensaje.enviados[0]


async def test_stats_sin_argumentos_ofrece_rangos(settings: Settings) -> None:
    mensaje = FakeMessage()

    await handlers.cmd_stats(FakeUpdate(mensaje), FakeContext(FakeApp(settings)))  # type: ignore[arg-type]

    assert mensaje.teclados[0] is not None


def test_los_teclados_caben_en_el_limite_de_64_bytes() -> None:
    """`callback_data` no admite mas, y pasarse rompe el mensaje entero."""
    teclados = [
        menu_fetch(["reddit", "lemmy", "bluesky", "instagram"]),
        menu_cantidad("instagram"),
        menu_stats(),
        acciones_de_publicacion("reddit", "1r4jnof"),
    ]
    for teclado in teclados:
        assert teclado is not None
        for fila in teclado.inline_keyboard:
            for boton in fila:
                assert len(str(boton.callback_data).encode()) <= 64


def test_sin_botones_si_el_id_no_cabe() -> None:
    """Mejor publicar sin acciones que que Telegram rechace el mensaje."""
    assert acciones_de_publicacion("reddit", "x" * 100) is None


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
async def test_un_extrano_no_puede_pulsar_los_botones(settings: Settings) -> None:
    """Los botones viajan en el mensaje: en un canal cualquiera podria pulsarlos."""
    query = FakeQuery(f"{ACCION_BORRAR}:reddit:abc")
    update = FakeUpdate(user_id=999)
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(FakeApp(settings), admins={1}))  # type: ignore[arg-type]

    assert query.respuestas[0][0] == "No estas autorizado."
    assert query.respuestas[0][1] is True  # con alerta, no en silencio


async def test_el_boton_de_cantidad_lanza_el_pipeline(settings: Settings) -> None:
    app = FakeApp(settings)
    query = FakeQuery("f:reddit:3")
    update = FakeUpdate()
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(app))  # type: ignore[arg-type]

    assert app.runs == [{"limit": 3}]


async def test_cancelar_no_lanza_nada(settings: Settings) -> None:
    app = FakeApp(settings)
    query = FakeQuery("x")
    update = FakeUpdate()
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(app))  # type: ignore[arg-type]

    assert app.runs == []
    assert "Cancelado" in query.editados[0]


async def test_siempre_se_responde_al_callback(settings: Settings) -> None:
    """Sin responder, Telegram deja el boton girando y parece colgado."""
    query = FakeQuery("st:7")
    update = FakeUpdate()
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(FakeApp(settings)))  # type: ignore[arg-type]

    assert query.respuestas


async def test_un_boton_desconocido_no_rompe_nada(settings: Settings) -> None:
    query = FakeQuery("inventado:cosa")
    update = FakeUpdate()
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(FakeApp(settings)))  # type: ignore[arg-type]

    assert query.respuestas[0][1] is True


async def test_las_stats_por_boton_respetan_el_rango(settings: Settings) -> None:
    query = FakeQuery("st:0")  # 0 = historico completo
    update = FakeUpdate()
    update.callback_query = query

    await callbacks.on_callback(update, FakeContext(FakeApp(settings)))  # type: ignore[arg-type]

    assert "Historico completo" in query.editados[0]
