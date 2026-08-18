"""El aviso de arranque por Telegram.

Existe por el autoarranque: Scrappy se levanta sin ventana y sin nadie mirando,
y hasta ahora la unica forma de saber si habia arrancado era abrir el log. Lo
que se prueba aqui es sobre todo **a donde va** y **que no puede tumbar el
arranque**, que son las dos formas en que un aviso hace mas dano que bien.
"""

from __future__ import annotations

from typing import Any

import pytest
from telegram.error import TelegramError

from scrappy.bot.avisos import avisar_arranque, texto_arranque
from scrappy.config.settings import Settings


class BotFalso:
    """Apunta a quien se le escribio y con que."""

    def __init__(self, *, falla_en: set[int] | None = None) -> None:
        self.enviados: list[tuple[int, str]] = []
        self._falla_en = falla_en or set()

    async def send_message(self, *, chat_id: int, text: str, **_kwargs: Any) -> None:
        if chat_id in self._falla_en:
            raise TelegramError("Chat not found")
        self.enviados.append((chat_id, text))


class SchedulerFalso:
    running = True
    next_run_at = "hoy a las 21:20"


class AppFalsa:
    """Lo poco que el aviso necesita de `ScrappyApp`."""

    def __init__(self, settings: Settings, bot: BotFalso | None) -> None:
        self.settings = settings
        self.bot = bot
        self.paused = False


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "telegram_bot_token": "123456789:TOKEN-DE-PRUEBA-NO-REAL",
        "telegram_target_chat_id": "-1001111111111",
        "telegram_admin_ids": "111,222",
        "state_backend": "memory",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _app(**kwargs: object) -> Any:
    bot = kwargs.pop("bot", BotFalso())
    return AppFalsa(_settings(**kwargs), bot)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A donde va
# ---------------------------------------------------------------------------
async def test_el_aviso_va_a_los_administradores() -> None:
    app = _app()

    enviados = await avisar_arranque(app, SchedulerFalso())

    assert enviados == 2
    assert [chat for chat, _ in app.bot.enviados] == [111, 222]


async def test_el_aviso_no_va_al_canal() -> None:
    """El canal es para el contenido.

    Meterle «he arrancado» seria ruido para todo el que lo siga, y ademas cada
    reinicio del equipo dejaria un mensaje de operacion en un sitio publico.
    """
    app = _app()

    await avisar_arranque(app, SchedulerFalso())

    destinos = {chat for chat, _ in app.bot.enviados}
    assert app.settings.telegram_target_chat_id not in {str(d) for d in destinos}


async def test_sin_administradores_no_se_manda_nada() -> None:
    """Antes que caer al canal, no avisar."""
    app = _app(telegram_admin_ids="")

    assert await avisar_arranque(app, SchedulerFalso()) == 0
    assert app.bot.enviados == []


async def test_se_puede_apagar() -> None:
    app = _app(notify_on_start=False)

    assert await avisar_arranque(app, SchedulerFalso()) == 0
    assert app.bot.enviados == []


# ---------------------------------------------------------------------------
# Que no tumbe el arranque
# ---------------------------------------------------------------------------
async def test_un_administrador_que_no_ha_hablado_con_el_bot_no_rompe_nada() -> None:
    """Telegram no deja escribir primero a quien no te ha escrito nunca.

    Es el fallo mas probable de todos, y no tiene nada que ver con si Scrappy
    arranco bien: el resto de avisos tienen que salir igual.
    """
    app = _app(bot=BotFalso(falla_en={111}))

    enviados = await avisar_arranque(app, SchedulerFalso())

    assert enviados == 1
    assert [chat for chat, _ in app.bot.enviados] == [222]


async def test_sin_bot_no_levanta() -> None:
    """`scrappy run` sin credenciales no deberia morir por no poder avisar."""
    app = _app(bot=None)

    assert await avisar_arranque(app, SchedulerFalso()) == 0


@pytest.mark.parametrize("fallan", [set(), {111, 222}])
async def test_nunca_levanta_pase_lo_que_pase(fallan: set[int]) -> None:
    app = _app(bot=BotFalso(falla_en=fallan))
    await avisar_arranque(app, SchedulerFalso())


# ---------------------------------------------------------------------------
# Que dice
# ---------------------------------------------------------------------------
def test_dice_cuando_arranco_y_cuando_publicara() -> None:
    """Son las dos preguntas que uno se hace al ver el aviso.

    La hora confirma que el aviso es de este reinicio y no de otro; la proxima
    ronda dice si hay que esperar o si algo va mal.
    """
    texto = texto_arranque(_app(), SchedulerFalso(), escuchando=True)

    assert "Scrappy en marcha" in texto
    assert "Arrancado:" in texto
    assert "hoy a las 21:20" in texto


def test_avisa_si_arranco_sin_escuchar_comandos() -> None:
    """Con `--no-bot` los botones de las publicaciones no responden.

    Sin decirlo, pareceria que el bot esta roto.
    """
    texto = texto_arranque(_app(), SchedulerFalso(), escuchando=False)
    assert "No escucho comandos" in texto


def test_el_texto_va_en_html_valido() -> None:
    """Se manda con `parse_mode=HTML`: una etiqueta mal cerrada y Telegram lo
    rechaza entero, justo el mensaje que avisa de que algo funciona."""
    from xml.etree import ElementTree

    texto = texto_arranque(_app(), SchedulerFalso(), escuchando=True)
    ElementTree.fromstring(f"<raiz>{texto}</raiz>")
