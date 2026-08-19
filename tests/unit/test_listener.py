"""Que Scrappy escuche, no solo que publique.

El fallo que motiva este fichero: `ScrappyApp` construye un `Bot` con el que
enviar, pero recibir necesita ademas un `Application` haciendo polling, y eso
solo lo montaba `scrappy run`. Desde la TUI se publicaba con normalidad y
ningun boton respondia — no habia nadie al otro lado.

Costo verlo porque cada mitad funcionaba: el manejador hacia lo correcto
cuando le llegaba una pulsacion, y publicar funcionaba. El hueco estaba entre
las dos cosas, que es justo donde no miraba ningun test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from telegram.error import Conflict, TimedOut

from scrappy.bot.listener import BotListener
from scrappy.tui.main import ScrappyTUI

ENV = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=123456789:TOKEN-DE-PRUEBA-NO-REAL
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_STATE_BACKEND=memory
SCRAPPY_SCHEDULE_ENABLED=false
"""


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")

    ruta = tmp_path / ".env"
    ruta.write_text(f"{ENV}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n", encoding="utf-8")
    return ruta


class ApplicationFalsa:
    """Doble del `Application` de python-telegram-bot.

    Se sustituye el constructor entero: montar uno de verdad abre conexiones
    con Telegram, que es exactamente lo que un test no debe hacer.
    """

    instancias: ClassVar[list[ApplicationFalsa]] = []

    def __init__(self) -> None:
        self.bot_data: dict[str, Any] = {}
        self.handlers: list[Any] = []
        self.updater = UpdaterFalso()
        self.running = False
        self.post_init: Any = None
        self.parado = False
        ApplicationFalsa.instancias.append(self)

    def add_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False
        self.parado = True

    async def shutdown(self) -> None:
        pass


class UpdaterFalso:
    def __init__(self) -> None:
        self.running = False
        self.opciones: dict[str, Any] = {}
        self.error_callback: Any = None

    async def start_polling(self, **kwargs: Any) -> None:
        self.running = True
        self.opciones = kwargs
        self.error_callback = kwargs.get("error_callback")

    async def stop(self) -> None:
        self.running = False


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> type[ApplicationFalsa]:
    """Sustituye el `ApplicationBuilder` por uno que no sale a la red."""
    from scrappy.bot import listener as modulo

    ApplicationFalsa.instancias = []

    class BuilderFalso:
        def token(self, _token: str) -> BuilderFalso:
            return self

        def build(self) -> ApplicationFalsa:
            return ApplicationFalsa()

    monkeypatch.setattr(modulo, "ApplicationBuilder", BuilderFalso)
    return ApplicationFalsa


# ---------------------------------------------------------------------------
# El listener
# ---------------------------------------------------------------------------
async def test_arrancar_deja_el_polling_en_marcha(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    from scrappy.app import ScrappyApp

    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app)
        assert await listener.start() is True
        assert listener.running

        aplicacion = sin_red.instancias[-1]
        assert aplicacion.running
        assert aplicacion.updater.running
    finally:
        await listener.stop()
        await app.aclose()


async def test_se_registran_los_manejadores(settings: Any, sin_red: type[ApplicationFalsa]) -> None:
    """Sin esto el bot escucharia y no sabria que hacer con lo que oye."""
    from scrappy.app import ScrappyApp

    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app)
        await listener.start()

        aplicacion = sin_red.instancias[-1]
        # Los diez comandos, el de callbacks y el de errores.
        assert len(aplicacion.handlers) >= 12
        assert aplicacion.bot_data["scrappy_app"] is app
    finally:
        await listener.stop()
        await app.aclose()


async def test_se_descarta_lo_pendiente_al_arrancar(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    """Procesar de golpe lo de horas atras publicaria varias veces seguidas."""
    from scrappy.app import ScrappyApp

    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app)
        await listener.start()
        assert sin_red.instancias[-1].updater.opciones["drop_pending_updates"] is True
    finally:
        await listener.stop()
        await app.aclose()


async def test_parar_cierra_el_updater_y_la_aplicacion(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    from scrappy.app import ScrappyApp

    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app)
        await listener.start()
        aplicacion = sin_red.instancias[-1]

        await listener.stop()

        assert not listener.running
        assert not aplicacion.updater.running
        assert aplicacion.parado
    finally:
        await app.aclose()


async def test_parar_dos_veces_no_revienta(settings: Any, sin_red: type[ApplicationFalsa]) -> None:
    from scrappy.app import ScrappyApp

    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app)
        await listener.start()
        await listener.stop()
        await listener.stop()
    finally:
        await app.aclose()


async def test_sin_token_no_se_intenta_escuchar(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    """Sin credenciales, la TUI sigue siendo util para explorar y calibrar."""
    from scrappy.app import ScrappyApp

    vacio = settings.model_copy(
        update={"telegram_bot_token": type(settings.telegram_bot_token)("")}
    )
    app = await ScrappyApp.create(vacio, with_publisher=False)
    try:
        listener = BotListener(app)
        assert await listener.start() is False
        assert sin_red.instancias == []
    finally:
        await app.aclose()


# ---------------------------------------------------------------------------
# Dos procesos escuchando
# ---------------------------------------------------------------------------
async def test_otro_scrappy_escuchando_se_detecta_y_se_dice(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    """Telegram entrega cada actualizacion una vez: al segundo le da 409.

    Pasa en cuanto se tiene el arranque automatico puesto y ademas se abre la
    TUI, que no es un caso raro. Callarse dejaria dos Scrappys peleandose por
    cada pulsacion sin que nadie supiera por que fallan a medias.
    """
    from scrappy.app import ScrappyApp

    avisos: list[str] = []
    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app, on_conflict=avisos.append)
        await listener.start()

        # Lo que hace el updater cuando Telegram responde 409.
        sin_red.instancias[-1].updater.error_callback(Conflict("terminated by other getUpdates"))

        assert listener.conflicto is not None
        assert "Otro Scrappy" in listener.conflicto
        assert avisos == [listener.conflicto]
    finally:
        await listener.stop()
        await app.aclose()


async def test_un_fallo_pasajero_no_se_confunde_con_un_conflicto(
    settings: Any, sin_red: type[ApplicationFalsa]
) -> None:
    """Un timeout es normal en una conexion larga; no hay nada que anunciar."""
    from scrappy.app import ScrappyApp

    avisos: list[str] = []
    app = await ScrappyApp.create(settings, with_publisher=False)
    try:
        listener = BotListener(app, on_conflict=avisos.append)
        await listener.start()

        sin_red.instancias[-1].updater.error_callback(TimedOut())

        assert listener.conflicto is None
        assert avisos == []
    finally:
        await listener.stop()
        await app.aclose()


# ---------------------------------------------------------------------------
# Desde la TUI
# ---------------------------------------------------------------------------
async def test_la_tui_escucha_cuando_se_le_pide(
    env_path: Path, sin_red: type[ApplicationFalsa]
) -> None:
    """El arreglo, en una linea: la TUI publicaba sin escuchar."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False, escuchar=True).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.listener is not None
        assert tui.listener.running
        assert sin_red.instancias[-1].updater.running


async def test_escuchar_hay_que_pedirlo(env_path: Path, sin_red: type[ApplicationFalsa]) -> None:
    """Por defecto no: dos procesos con el mismo token se roban las pulsaciones.

    Un test que escuchara sin querer se las quitaria al bot de quien lo
    ejecuta, que puede ser el suyo de verdad.
    """
    async with ScrappyTUI(env_path=env_path, show_wizard=False).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert tui.listener is None
        assert sin_red.instancias == []


async def test_al_cerrar_la_tui_se_deja_de_escuchar(
    env_path: Path, sin_red: type[ApplicationFalsa]
) -> None:
    """Si no, el proceso no termina de morir y el token queda ocupado."""
    tui = ScrappyTUI(env_path=env_path, show_wizard=False, escuchar=True)
    async with tui.run_test():
        assert tui.listener is not None

    assert tui.listener is None
    assert not sin_red.instancias[-1].updater.running


async def test_recargar_no_deja_dos_escuchas(
    env_path: Path, sin_red: type[ApplicationFalsa]
) -> None:
    """La recarga monta todo de nuevo: la escucha anterior tiene que cerrarse."""
    async with ScrappyTUI(env_path=env_path, show_wizard=False, escuchar=True).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        primera = sin_red.instancias[-1]

        await tui.recargar()
        await pilot.pause()

        assert not primera.updater.running, "la escucha anterior sigue viva"
        assert tui.listener is not None
        assert tui.listener.running
        assert sin_red.instancias[-1] is not primera


async def test_sin_telegram_configurado_no_escucha(
    tmp_path: Path, sin_red: type[ApplicationFalsa]
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SCRAPPY_STATE_BACKEND=memory\n", encoding="utf-8")

    async with ScrappyTUI(env_path=env_path, show_wizard=False, escuchar=True).run_test() as pilot:
        tui: ScrappyTUI = pilot.app  # type: ignore[assignment]
        assert not tui.can_publish
        assert tui.listener is None


# ---------------------------------------------------------------------------
# Arrancar cuando la red aun no esta
# ---------------------------------------------------------------------------
class ListenerTardio:
    """Un listener que no conecta hasta el intento `bueno`."""

    def __init__(self, bueno: int) -> None:
        self.bueno = bueno
        self.intentos = 0

    async def start(self) -> bool:
        self.intentos += 1
        return self.intentos >= self.bueno


async def test_se_insiste_si_la_red_no_esta_lista_al_iniciar_sesion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El autoarranque se lanza antes de que el wifi este asociado.

    Rendirse al primer intento dejaba a Scrappy sin escuchar hasta el siguiente
    inicio de sesion: un dia entero de bot mudo por tres segundos de red.
    """
    from scrappy import cli

    esperas: list[float] = []

    async def _dormir(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr(cli.asyncio, "sleep", _dormir)
    listener = ListenerTardio(bueno=3)

    assert await cli._escuchar_con_reintentos(listener)  # type: ignore[arg-type]
    assert listener.intentos == 3
    assert len(esperas) == 2


async def test_se_acaba_rindiendo_en_vez_de_insistir_para_siempre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un token mal escrito no se arregla esperando."""
    from scrappy import cli

    async def _dormir(_segundos: float) -> None:
        return None

    monkeypatch.setattr(cli.asyncio, "sleep", _dormir)
    listener = ListenerTardio(bueno=999)

    assert not await cli._escuchar_con_reintentos(listener)  # type: ignore[arg-type]
    assert listener.intentos == cli._INTENTOS_CONEXION


# ---------------------------------------------------------------------------
# Montar cuando la red no esta
# ---------------------------------------------------------------------------
async def test_un_montaje_colgado_no_deja_el_proceso_ahi_para_siempre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El fallo que dejo a Scrappy sin arrancar tras un reinicio.

    Construir la aplicacion habla con Telegram para inicializar el bot. Al
    encender el equipo, con el wifi aun sin asociar, esa llamada se quedo
    esperando para siempre: el proceso ni arrancaba ni terminaba, asi que no
    habia ni bot ni forma de que la tarea del sistema lo reintentara.
    """
    from scrappy import cli

    async def _no_vuelve_nunca(*_args: object, **_kwargs: object) -> None:
        # Un evento que nadie levanta, y no `sleep`: la prueba sustituye
        # `sleep` para no esperar de verdad, y el doble se lo saltaria.
        await asyncio.Event().wait()

    esperas: list[float] = []

    async def _dormir(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr(cli.ScrappyApp, "create", _no_vuelve_nunca)
    monkeypatch.setattr(cli, "_ESPERA_MONTAJE", 0.01)
    monkeypatch.setattr(cli.asyncio, "sleep", _dormir)

    assert await cli._montar_con_reintentos(object()) is None  # type: ignore[arg-type]
    assert len(esperas) == cli._INTENTOS_MONTAJE - 1


async def test_si_la_red_vuelve_a_tiempo_se_monta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Insistir tiene que servir de algo: al segundo intento ya hay red."""
    from scrappy import cli

    intentos = 0

    async def _tarda_una_vez(*_args: object, **_kwargs: object) -> str:
        nonlocal intentos
        intentos += 1
        if intentos == 1:
            await asyncio.Event().wait()
        return "montada"

    async def _dormir(_segundos: float) -> None:
        return None

    monkeypatch.setattr(cli.ScrappyApp, "create", _tarda_una_vez)
    monkeypatch.setattr(cli, "_ESPERA_MONTAJE", 0.01)
    monkeypatch.setattr(cli.asyncio, "sleep", _dormir)

    assert await cli._montar_con_reintentos(object()) == "montada"  # type: ignore[arg-type]


async def test_un_error_de_configuracion_no_se_reintenta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Esperar no arregla un token mal escrito, y disimularlo con seis
    intentos solo retrasaria el mensaje que explica que pasa."""
    from scrappy import cli
    from scrappy.core.errors import ConfigError

    async def _configuracion_rota(*_args: object, **_kwargs: object) -> None:
        raise ConfigError("falta el token")

    monkeypatch.setattr(cli.ScrappyApp, "create", _configuracion_rota)

    with pytest.raises(ConfigError):
        await cli._montar_con_reintentos(object())  # type: ignore[arg-type]
