"""Configurar las fuentes desde Telegram.

Lo que se prueba aqui es sobre todo lo que puede salir mal callando: que un
indice viejo no borre el elemento equivocado, que el interruptor no prometa
mas de lo que hace, y que la puerta del aviso legal no se pueda abrir con un
toque en el movil.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from scrappy.app import ScrappyApp
from scrappy.bot import callbacks_fuentes as cf
from scrappy.bot.fuentes import ORIGENES, ValorNoValido, normalizar, requiere_ack_de_tos
from scrappy.bot.keyboards import confirmar_quitar, lista_origenes
from scrappy.config.settings import Settings

CATALOGO = """\
# El catalogo de Scrappy. Los comentarios de este fichero tienen que
# sobrevivir a cualquier edicion desde el bot.
ranking:
  weights:
    engagement: 0.5
    velocity: 0.5

sources:
  reddit:
    # De donde saca los memes
    subreddits:
      - memes
      - dankmemes
    subreddits_per_run: 2
  lemmy:
    communities:
      - memes@lemmy.world
  youtube:
    queries:
      - shorts graciosos

filters:
  blocked_authors: []
"""


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------
class FakeQuery:
    def __init__(self) -> None:
        self.respuestas: list[str] = []
        self.textos: list[str] = []
        self.teclados: list[Any] = []
        self.message: Any = None

    async def answer(self, text: str = "", **_kwargs: Any) -> None:
        self.respuestas.append(text)

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.textos.append(text)
        self.teclados.append(kwargs.get("reply_markup"))


class FakeUpdate:
    def __init__(self, query: FakeQuery) -> None:
        self.callback_query = query


class FakeContext:
    def __init__(self, app: ScrappyApp, *, recargador: Any = None) -> None:
        self.bot_data: dict[str, Any] = {"scrappy_app": app}
        if recargador is not None:
            self.bot_data["solicitar_recarga"] = recargador
        self.user_data: dict[str, Any] = {}


class FakeMensaje:
    """Un mensaje del usuario que responde a una pregunta del bot."""

    def __init__(self, texto: str, responde_a: int | None = None) -> None:
        self.text = texto
        self.enviados: list[str] = []
        self.reply_to_message = (
            None
            if responde_a is None
            else type("Previo", (), {"message_id": responde_a, "text": cf.PROMPT})()
        )

    async def reply_text(self, text: str, **_kwargs: Any) -> Any:
        self.enviados.append(text)
        return self


class FakeUpdateMensaje:
    def __init__(self, mensaje: FakeMensaje) -> None:
        self.effective_message = mensaje


@pytest.fixture
def entorno(tmp_path: Path) -> tuple[Path, Path]:
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(CATALOGO, encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SCRAPPY_TELEGRAM_BOT_TOKEN=123456789:TOKEN-DE-PRUEBA-NO-REAL\n"
        "SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148\n"
        "SCRAPPY_STATE_BACKEND=memory\n"
        "SCRAPPY_GIPHY_ENABLED=false\n",
        encoding="utf-8",
    )
    return yaml_path, env_path


@pytest.fixture
async def app(entorno: tuple[Path, Path]) -> Any:
    yaml_path, env_path = entorno
    settings = Settings(_env_file=env_path, sources_config_path=yaml_path, state_backend="memory")
    scrappy = await ScrappyApp.create(settings, with_publisher=False, env_path=env_path)
    try:
        yield scrappy
    finally:
        await scrappy.aclose()


# ---------------------------------------------------------------------------
# El limite de 64 bytes
# ---------------------------------------------------------------------------
def test_el_peor_callback_data_posible_cabe() -> None:
    """Telegram rechaza el mensaje entero si se pasa de 64 bytes.

    Por eso los botones llevan posiciones y no nombres: una consulta de
    Bluesky ocupa ella sola mas de la mitad del presupuesto.
    """
    peor = confirmar_quitar("instagram", 9, 49).inline_keyboard[0][0].callback_data
    assert peor is not None
    assert len(peor.encode()) <= 64


def test_una_consulta_larga_no_entra_en_el_callback_data() -> None:
    """La prueba que justifica los indices: con el valor dentro no cabria."""
    consulta = "(funny OR meme) has:videos -is:retweet lang:es min_faves:2000"
    teclado = lista_origenes("bluesky", 0, [consulta], se_puede_anadir=True)

    datos = teclado.inline_keyboard[0][0].callback_data
    assert datos is not None
    assert len(datos.encode()) <= 64
    assert consulta not in datos


def test_el_orden_de_los_origenes_es_un_contrato() -> None:
    """Los `callback_data` llevan la posicion del campo.

    Reordenar esta tabla haria que un boton ya enviado apuntara a otro campo:
    pulsar «quitar» sobre las busquedas de YouTube borraria un canal.
    """
    assert ORIGENES["youtube"][0][0] == "queries"
    assert ORIGENES["youtube"][1][0] == "channels"
    assert ORIGENES["reddit"][0][0] == "subreddits"


# ---------------------------------------------------------------------------
# Normalizar lo que se escribe
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("campo", "escrito", "esperado"),
    [
        ("subreddits", " r/DankMemes ", "DankMemes"),
        ("subreddits", "/r/funny", "funny"),
        ("subreddits", "https://www.reddit.com/r/funny/", "funny"),
        ("hashtags", "#gatos", "gatos"),
        ("accounts", "@alguien", "alguien"),
    ],
)
def test_se_acepta_como_lo_escribe_la_gente(campo: str, escrito: str, esperado: str) -> None:
    """«r/memes» es como se ve en Reddit, y guardarlo tal cual da una URL rota.

    El fallo no se veria hasta la ronda siguiente, en un log que nadie mira.
    """
    assert normalizar(campo, escrito) == esperado


@pytest.mark.parametrize(
    ("campo", "escrito"),
    [("subreddits", "no vale!"), ("subreddits", " "), ("communities", "sin-instancia")],
)
def test_lo_que_no_sirve_se_rechaza_diciendo_por_que(campo: str, escrito: str) -> None:
    with pytest.raises(ValorNoValido):
        normalizar(campo, escrito)


# ---------------------------------------------------------------------------
# Editar origenes
# ---------------------------------------------------------------------------
async def test_quitar_pide_confirmacion_antes(app: ScrappyApp) -> None:
    """Quitar no se deshace, asi que se pregunta, como en todo lo demas."""
    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "sq", ["sq", "reddit", "0", "0"])

    assert "memes" in query.textos[-1]
    assert app.sources_config.for_source("reddit").get_list("subreddits") == [
        "memes",
        "dankmemes",
    ]


async def test_quitar_confirmado_se_aplica_sin_reiniciar(app: ScrappyApp) -> None:
    """Y llega hasta el adapter, que es donde importa.

    El catalogo ya se releia solo, pero los adapters guardaban su config del
    arranque: cambiar los subreddits no surtia efecto hasta reconstruir la
    aplicacion, mientras la interfaz decia que si.
    """
    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "sqc", ["sqc", "reddit", "0", "0"])

    reddit = next(a for a in app.adapters if a.name == "reddit")
    assert reddit.config.get_list("subreddits") == ["dankmemes"]


async def test_un_indice_viejo_no_borra_lo_que_no_es(app: ScrappyApp, entorno: Any) -> None:
    """El teclado lleva posiciones, y el fichero puede haber cambiado.

    Sin comprobar el rango, pulsar un boton de hace media hora borraria el
    elemento que ahora ocupa ese sitio, o reventaria.
    """
    yaml_path, _ = entorno
    yaml_path.write_text(CATALOGO.replace("      - dankmemes\n", ""), encoding="utf-8")

    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "sqc", ["sqc", "reddit", "0", "5"])

    assert "ya no esta" in query.respuestas[0]


async def test_anadir_escribe_el_yaml_conservando_los_comentarios(
    app: ScrappyApp, entorno: Any
) -> None:
    """El fichero es de quien lo escribio: sus comentarios no son desechables."""
    yaml_path, _ = entorno
    contexto = FakeContext(app)
    contexto.user_data[cf.CLAVE_PENDIENTES] = {77: ("reddit", 0)}

    mensaje = FakeMensaje("r/ProgrammerHumor", responde_a=77)
    await cf.on_respuesta(FakeUpdateMensaje(mensaje), contexto)

    guardado = yaml_path.read_text(encoding="utf-8")
    assert "# De donde saca los memes" in guardado
    assert "ProgrammerHumor" in guardado
    assert app.sources_config.for_source("reddit").get_list("subreddits")[-1] == "ProgrammerHumor"


async def test_lo_que_ya_estaba_no_se_duplica(app: ScrappyApp) -> None:
    contexto = FakeContext(app)
    contexto.user_data[cf.CLAVE_PENDIENTES] = {77: ("reddit", 0)}

    mensaje = FakeMensaje("MEMES", responde_a=77)
    await cf.on_respuesta(FakeUpdateMensaje(mensaje), contexto)

    assert "ya estaba" in mensaje.enviados[-1]
    assert app.sources_config.for_source("reddit").get_list("subreddits") == [
        "memes",
        "dankmemes",
    ]


async def test_una_clave_que_no_esta_en_el_yaml_se_dice(app: ScrappyApp) -> None:
    """El editor no crea claves nuevas, y fingir que se guardo seria mentir."""
    contexto = FakeContext(app)
    # youtube tiene `queries` en el catalogo de prueba, pero no `channels`.
    contexto.user_data[cf.CLAVE_PENDIENTES] = {77: ("youtube", 1)}

    mensaje = FakeMensaje("uncanal", responde_a=77)
    await cf.on_respuesta(FakeUpdateMensaje(mensaje), contexto)

    assert "no hay seccion" in mensaje.enviados[-1].lower()


# ---------------------------------------------------------------------------
# Responder al bot
# ---------------------------------------------------------------------------
async def test_una_respuesta_a_otra_cosa_se_ignora_en_silencio(app: ScrappyApp) -> None:
    """Es el unico MessageHandler del bot.

    Si contestara a cualquier respuesta seria insoportable en un grupo, y
    ademas revelaria que esta ahi a quien no es administrador.
    """
    contexto = FakeContext(app)
    contexto.user_data[cf.CLAVE_PENDIENTES] = {77: ("reddit", 0)}

    mensaje = FakeMensaje("hola que tal", responde_a=1234)
    mensaje.reply_to_message = type("Previo", (), {"message_id": 1234, "text": "otra cosa"})()
    await cf.on_respuesta(FakeUpdateMensaje(mensaje), contexto)

    assert mensaje.enviados == []


async def test_un_aviso_de_antes_de_recargar_se_explica(app: ScrappyApp) -> None:
    """`user_data` vive en el Application, y recargar estrena uno.

    Sin esto, el usuario responde a la pregunta del bot y no pasa nada: el
    peor final posible.
    """
    contexto = FakeContext(app)  # sin pendientes: como tras una recarga
    mensaje = FakeMensaje("memes2", responde_a=77)
    await cf.on_respuesta(FakeUpdateMensaje(mensaje), contexto)

    assert "antes de recargar" in mensaje.enviados[-1]


# ---------------------------------------------------------------------------
# El interruptor
# ---------------------------------------------------------------------------
async def test_encender_una_fuente_escribe_el_env_y_pide_recarga(
    app: ScrappyApp, entorno: Any
) -> None:
    _, env_path = entorno
    pedidas: list[str] = []
    contexto = FakeContext(app, recargador=lambda motivo: pedidas.append(motivo) or True)

    await cf.despachar(FakeUpdate(FakeQuery()), contexto, "so", ["so", "giphy", "1"])

    assert "SCRAPPY_GIPHY_ENABLED=true" in env_path.read_text(encoding="utf-8")
    assert pedidas == ["se activo giphy"]


async def test_sin_recargador_se_dice_la_verdad(app: ScrappyApp) -> None:
    """Prometer «ya esta activo» cuando no lo esta es el fallo que este
    proyecto lleva corrigiendo desde el boton de vetar autores."""
    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "so", ["so", "giphy", "1"])

    assert "al reiniciar" in query.respuestas[-1]


async def test_una_fuente_tras_el_flag_de_tos_no_se_activa_desde_el_movil(
    app: ScrappyApp, entorno: Any
) -> None:
    """El flag es un consentimiento informado.

    Uno que se da pulsando un boton sin leer nada no es un consentimiento, asi
    que aqui no se escribe **nada** y se explica donde mirar.
    """
    _, env_path = entorno
    antes = env_path.read_text(encoding="utf-8")
    query = FakeQuery()

    await cf.despachar(FakeUpdate(query), FakeContext(app), "so", ["so", "youtube", "1"])

    assert "LEGAL" in query.respuestas[-1]
    assert env_path.read_text(encoding="utf-8") == antes


def test_la_api_de_x_no_esta_tras_el_flag_pero_el_scrape_si() -> None:
    """La API oficial es un uso permitido; el scrape no.

    Es la unica fuente donde depende de como este configurada, y por eso se
    pregunta al registro en vez de mantener una lista a mano.
    """
    base = {
        "telegram_bot_token": "1:x",
        "telegram_target_chat_id": "1",
        "state_backend": "memory",
    }
    assert not requiere_ack_de_tos("x", Settings(**base, x_backend="api"))  # type: ignore[arg-type]
    assert requiere_ack_de_tos("x", Settings(**base, x_backend="scrape"))  # type: ignore[arg-type]


async def test_una_fuente_inventada_no_llega_al_yaml(app: ScrappyApp) -> None:
    """Los callbacks vienen de un mensaje, y un nombre inventado no debe
    convertirse en una ruta dentro del fichero."""
    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "sf", ["sf", "../../etc/passwd"])

    assert "No conozco esa fuente." in query.respuestas[-1]


# ---------------------------------------------------------------------------
# El catalogo real
# ---------------------------------------------------------------------------
def test_todas_las_fuentes_tienen_sus_campos_declarados() -> None:
    """Una fuente sin entrada en la tabla saldria en el menu sin nada que
    editar, y nadie se enteraria hasta pulsarla."""
    from scrappy.sources.registry import iter_adapter_names

    faltan = [n for n in iter_adapter_names() if n not in ORIGENES]
    assert faltan == []


def test_los_campos_declarados_existen_en_el_catalogo_de_ejemplo(tmp_path: Path) -> None:
    """Si el ejemplo no trae la clave, el boton «anadir» no podria escribir.

    El editor no crea claves nuevas a proposito, asi que la tabla y el fichero
    de ejemplo tienen que estar de acuerdo.
    """
    import yaml

    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")

    copia = tmp_path / "sources.yaml"
    shutil.copy(ejemplo, copia)
    datos = yaml.safe_load(copia.read_text(encoding="utf-8"))["sources"]

    faltan = [
        f"{fuente}.{clave}"
        for fuente, campos in ORIGENES.items()
        if fuente in datos
        for clave, _ in campos
        if clave not in (datos[fuente] or {})
    ]
    assert faltan == [], f"campos declarados que no estan en el ejemplo: {faltan}"


# ---------------------------------------------------------------------------
# Que el riesgo siga siendo visible despues de aceptar el flag
# ---------------------------------------------------------------------------
def test_una_fuente_de_riesgo_encendida_no_se_pinta_como_las_demas() -> None:
    """El candado depende del flag; la advertencia, no.

    Quien activo `ENABLE_TOS_RISKY_SOURCES` hace meses ya no se acuerda, y sin
    esto TikTok se veia en el menu exactamente igual que Reddit.
    """
    from scrappy.bot.keyboards import menu_fuentes

    teclado = menu_fuentes(
        [
            ("reddit", True, False, False),
            ("tiktok", True, False, True),
            ("instagram", False, False, True),
            ("youtube", False, True, True),
        ]
    )
    etiquetas = [b.text for fila in teclado.inline_keyboard for b in fila]

    assert "✅ reddit" in etiquetas
    assert "⚠️ tiktok" in etiquetas
    assert "⚪ instagram" in etiquetas
    assert "🔒 youtube" in etiquetas


async def test_encender_una_de_riesgo_pide_confirmacion(app: ScrappyApp, entorno: Any) -> None:
    """Con el flag ya puesto, encender TikTok quedaba a un toque de Reddit."""
    _, env_path = entorno
    app.settings = app.settings.model_copy(update={"enable_tos_risky_sources": True})
    antes = env_path.read_text(encoding="utf-8")
    query = FakeQuery()

    await cf.despachar(FakeUpdate(query), FakeContext(app), "so", ["so", "youtube", "1"])

    # Preguntar, no escribir.
    assert env_path.read_text(encoding="utf-8") == antes
    assert "incumpliendo los terminos" in query.textos[-1]


async def test_confirmada_si_se_enciende(app: ScrappyApp, entorno: Any) -> None:
    _, env_path = entorno
    app.settings = app.settings.model_copy(update={"enable_tos_risky_sources": True})

    await cf.despachar(FakeUpdate(FakeQuery()), FakeContext(app), "soc", ["soc", "youtube"])

    assert "SCRAPPY_YOUTUBE_ENABLED=true" in env_path.read_text(encoding="utf-8")


async def test_apagar_una_de_riesgo_es_inmediato(app: ScrappyApp, entorno: Any) -> None:
    """Retirarse siempre es seguro: preguntar aqui solo estorbaria."""
    _, env_path = entorno
    app.settings = app.settings.model_copy(update={"enable_tos_risky_sources": True})

    await cf.despachar(FakeUpdate(FakeQuery()), FakeContext(app), "so", ["so", "youtube", "0"])

    assert "SCRAPPY_YOUTUBE_ENABLED=false" in env_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Renovar la sesion de X desde el movil
# ---------------------------------------------------------------------------
def test_el_boton_de_renovar_solo_sale_donde_aplica() -> None:
    """Un boton que no hace nada es peor que no tener boton."""
    from scrappy.bot.keyboards import ficha_fuente

    def _etiquetas(renovable: bool) -> list[str]:
        teclado = ficha_fuente(
            "x",
            encendida=True,
            campos=(("accounts", "cuentas"),),
            cuantos={"accounts": 2},
            renovable=renovable,
        )
        return [b.text for fila in teclado.inline_keyboard for b in fila]

    assert any("Renovar" in e for e in _etiquetas(True))
    assert not any("Renovar" in e for e in _etiquetas(False))


async def test_renovar_desde_telegram_usa_el_mismo_codigo(
    app: ScrappyApp, monkeypatch: Any
) -> None:
    """El boton, `scrappy cookies` y la TUI tienen que decir lo mismo."""
    from scrappy.config.settings import XBackend
    from scrappy.sources.x_cookies import RenovacionCookies

    app.settings = app.settings.model_copy(
        update={"x_backend": XBackend.SCRAPE, "enable_tos_risky_sources": True}
    )
    monkeypatch.setattr(
        "scrappy.sources.x_cookies.renovar_cookies",
        lambda _s: RenovacionCookies(True, "Sesion de X renovada: 17 cookies guardadas", de_x=17),
    )

    query = FakeQuery()
    await cf.despachar(FakeUpdate(query), FakeContext(app), "sck", ["sck", "x"])

    assert "17 cookies" in query.textos[-1]


async def test_no_se_renueva_lo_que_no_tiene_sesion(app: ScrappyApp) -> None:
    query = FakeQuery()

    await cf.despachar(FakeUpdate(query), FakeContext(app), "sck", ["sck", "reddit"])

    assert "no usa cookies" in query.respuestas[-1]
