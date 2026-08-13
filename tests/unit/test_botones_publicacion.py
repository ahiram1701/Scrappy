"""Los tres botones que acompanan a cada publicacion, de principio a fin.

Son con los que mas se interactua y los menos probados: los menus de `/fetch`
y `/stats` solo leen, mientras que estos borran un mensaje, escriben en
`sources.yaml` y dejan rastro en el historial.

Lo que se comprueba es el viaje completo: se genera el teclado de verdad, se
coge el `callback_data` que produce -no uno escrito a mano- y se mete por
`on_callback`. Asi, si alguien renombra una constante, el viaje se rompe aqui
y no en el movil de alguien.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from scrappy.bot import callbacks
from scrappy.config.loader import load_sources_config
from scrappy.config.settings import Settings, StateBackend
from scrappy.core.models import MediaKind, PublishedItem
from scrappy.delivery.keyboards import (
    ACCION_BORRAR,
    ACCION_DISLIKE,
    ACCION_VETAR,
    acciones_de_publicacion,
)
from tests.unit.test_bot_ux import FakeQuery, FakeUpdate

TOKEN = "8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU"


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------
def _item(
    *, source: str = "reddit", source_id: str = "1r4jnof", autor: str = "umberto"
) -> PublishedItem:
    return PublishedItem(
        source=source,
        source_id=source_id,
        permalink=f"https://example.test/{source_id}",
        sha256="a" * 64,
        phash=None,
        score=0.8,
        kind=MediaKind.VIDEO,
        author=autor,
        telegram_message_id=555,
        telegram_file_id="file-1",
    )


class EstadoFalso:
    def __init__(self, item: PublishedItem | None) -> None:
        self._item = item
        self.feedback: list[tuple[str, str]] = []
        self.votos: dict[str, int] = {}

    async def find(self, source: str, source_id: str) -> PublishedItem | None:
        if self._item is None:
            return None
        coincide = self._item.source == source and self._item.source_id == source_id
        return self._item if coincide else None

    async def record_feedback(self, item: PublishedItem, kind: str) -> None:
        self.feedback.append((item.uid, kind))
        if kind == "dislike" and item.author:
            self.votos[item.author] = self.votos.get(item.author, 0) + 1

    async def disliked_authors(self) -> dict[str, int]:
        return dict(self.votos)


class BotFalso:
    def __init__(self, *, falla: Exception | None = None) -> None:
        self.borrados: list[tuple[str, int]] = []
        self._falla = falla

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        if self._falla is not None:
            raise self._falla
        self.borrados.append((chat_id, message_id))


class AppFalsa:
    def __init__(self, settings: Settings, item: PublishedItem | None) -> None:
        self.settings = settings
        self.state = EstadoFalso(item)
        self.sources_config = load_sources_config(settings.sources_config_path)


class ContextoFalso:
    def __init__(self, app: AppFalsa, bot: BotFalso | None = None) -> None:
        self.bot_data = {
            "scrappy_app": app,
            "admin_ids": frozenset({1412545148}),
            "scheduler": None,
        }
        self.bot = bot or BotFalso()
        self.args: list[str] = []


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    ejemplo = Path("config/sources.example.yaml")
    if not ejemplo.exists():  # pragma: no cover
        pytest.skip("no se encuentra config/sources.example.yaml")
    destino = tmp_path / "sources.yaml"
    destino.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")
    return destino


@pytest.fixture
def settings(yaml_path: Path) -> Settings:
    return Settings(
        telegram_bot_token=SecretStr(TOKEN),
        telegram_target_chat_id="1412545148",
        telegram_admin_ids="1412545148",
        state_backend=StateBackend.MEMORY,
        sources_config_path=yaml_path,
    )


async def _pulsar(
    dato: str, app: AppFalsa, bot: BotFalso | None = None
) -> tuple[FakeQuery, ContextoFalso]:
    """Pulsa un boton por su `callback_data` y devuelve lo que respondio."""
    query = FakeQuery(dato)
    update = FakeUpdate()
    update.callback_query = query
    contexto = ContextoFalso(app, bot)
    await callbacks.on_callback(update, contexto)  # type: ignore[arg-type]
    return query, contexto


def _dato(teclado: Any, indice: int) -> str:
    return str(teclado.inline_keyboard[0][indice].callback_data)


# ---------------------------------------------------------------------------
# El viaje de ida y vuelta
# ---------------------------------------------------------------------------
def test_el_teclado_produce_los_tres_botones() -> None:
    teclado = acciones_de_publicacion("reddit", "1r4jnof")
    assert teclado is not None

    etiquetas = [b.text for b in teclado.inline_keyboard[0]]
    assert etiquetas == ["🗑 Borrar", "🚫 Vetar autor", "👎"]


@pytest.mark.parametrize(
    ("indice", "accion"), [(0, ACCION_BORRAR), (1, ACCION_VETAR), (2, ACCION_DISLIKE)]
)
def test_cada_boton_lleva_su_accion_y_el_item(indice: int, accion: str) -> None:
    """Si una constante se renombra sin tocar el otro lado, salta aqui."""
    teclado = acciones_de_publicacion("reddit", "1r4jnof")
    assert teclado is not None
    assert _dato(teclado, indice) == f"{accion}:reddit:1r4jnof"


@pytest.mark.parametrize("indice", [0, 1, 2], ids=["borrar", "vetar", "dislike"])
async def test_los_tres_botones_hacen_algo(
    settings: Settings, indice: int, yaml_path: Path
) -> None:
    """Ninguno puede quedarse sin manejador ni responder con un error."""
    teclado = acciones_de_publicacion("reddit", "1r4jnof")
    assert teclado is not None
    app = AppFalsa(settings, _item())

    query, _ = await _pulsar(_dato(teclado, indice), app)

    # Siempre se responde: si no, Telegram deja el boton girando.
    assert query.respuestas, "el boton no respondio"
    assert "No se que hacer" not in query.respuestas[0][0]
    assert "Fallo" not in query.respuestas[0][0]
    # Y deja rastro en el historial, que es lo que hace que el ranking aprenda.
    assert app.state.feedback


# ---------------------------------------------------------------------------
# Borrar
# ---------------------------------------------------------------------------
async def test_borrar_quita_el_mensaje_del_canal(settings: Settings) -> None:
    app = AppFalsa(settings, _item())
    bot = BotFalso()

    query, _ = await _pulsar(f"{ACCION_BORRAR}:reddit:1r4jnof", app, bot)

    assert bot.borrados == [("1412545148", 555)]
    assert query.respuestas[0][0] == "Borrado"


async def test_borrar_no_lo_saca_del_historial(settings: Settings) -> None:
    """A proposito: si se borrara, ese mismo meme volveria en la proxima ronda."""
    app = AppFalsa(settings, _item())

    await _pulsar(f"{ACCION_BORRAR}:reddit:1r4jnof", app)

    assert app.state.feedback == [("reddit:1r4jnof", "deleted")]
    # El item sigue encontrandose, que es lo que impide republicarlo.
    assert await app.state.find("reddit", "1r4jnof") is not None


async def test_borrar_pasadas_48_horas_lo_explica(settings: Settings) -> None:
    """Telegram no deja borrar mensajes viejos, y el motivo no es evidente."""
    from telegram.error import BadRequest

    app = AppFalsa(settings, _item())
    bot = BotFalso(falla=BadRequest("message can't be deleted"))

    query, _ = await _pulsar(f"{ACCION_BORRAR}:reddit:1r4jnof", app, bot)

    texto, alerta = query.respuestas[0]
    assert "48 horas" in texto
    assert alerta is True
    # Y no se apunta como borrado algo que sigue ahi.
    assert app.state.feedback == []


async def test_borrar_sin_id_de_mensaje_lo_dice(settings: Settings) -> None:
    item = _item()
    app = AppFalsa(settings, item.model_copy(update={"telegram_message_id": None}))

    query, _ = await _pulsar(f"{ACCION_BORRAR}:reddit:1r4jnof", app)

    assert "no se guardo el id" in query.respuestas[0][0]


# ---------------------------------------------------------------------------
# Vetar autor
# ---------------------------------------------------------------------------
async def test_vetar_escribe_en_el_yaml(settings: Settings, yaml_path: Path) -> None:
    """En el fichero y no solo en memoria: el veto tiene que sobrevivir."""
    app = AppFalsa(settings, _item(autor="umberto"))

    query, _ = await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)

    assert "umberto" in yaml_path.read_text(encoding="utf-8")
    assert "vetado" in query.respuestas[0][0]
    assert app.state.feedback == [("reddit:1r4jnof", "banned_author")]


async def test_vetar_deja_el_veto_activo_en_la_configuracion(
    settings: Settings, yaml_path: Path
) -> None:
    """No basta escribirlo: la configuracion en memoria tiene que recogerlo."""
    app = AppFalsa(settings, _item(autor="umberto"))

    await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)

    assert "umberto" in app.sources_config.filters.blocked_authors
    # Y releerlo del disco da lo mismo.
    assert "umberto" in load_sources_config(yaml_path).filters.blocked_authors


async def test_vetar_conserva_los_comentarios_del_yaml(settings: Settings, yaml_path: Path) -> None:
    """Son la documentacion de cada valor; perderlos seria un precio absurdo."""
    antes = yaml_path.read_text(encoding="utf-8").count("#")
    app = AppFalsa(settings, _item(autor="umberto"))

    await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)

    assert yaml_path.read_text(encoding="utf-8").count("#") == antes


async def test_vetar_dos_veces_no_duplica(settings: Settings, yaml_path: Path) -> None:
    app = AppFalsa(settings, _item(autor="umberto"))

    await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)
    query, _ = await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)

    assert "ya estaba vetado" in query.respuestas[0][0]
    assert yaml_path.read_text(encoding="utf-8").count("umberto") == 1


async def test_vetar_sin_autor_lo_explica(settings: Settings) -> None:
    """Lo publicado con versiones anteriores no guardaba el autor."""
    app = AppFalsa(settings, _item(autor=""))

    query, _ = await _pulsar(f"{ACCION_VETAR}:reddit:1r4jnof", app)

    assert "no se guardo el autor" in query.respuestas[0][0]


# ---------------------------------------------------------------------------
# No me gusta
# ---------------------------------------------------------------------------
async def test_el_pulgar_abajo_acumula(settings: Settings) -> None:
    """Es la version suave del veto: penaliza, no elimina."""
    app = AppFalsa(settings, _item(autor="umberto"))

    await _pulsar(f"{ACCION_DISLIKE}:reddit:1r4jnof", app)
    query, _ = await _pulsar(f"{ACCION_DISLIKE}:reddit:1r4jnof", app)

    assert app.state.votos == {"umberto": 2}
    assert "2 voto" in query.respuestas[0][0]


async def test_el_pulgar_abajo_no_borra_ni_veta(settings: Settings, yaml_path: Path) -> None:
    app = AppFalsa(settings, _item(autor="umberto"))
    bot = BotFalso()

    await _pulsar(f"{ACCION_DISLIKE}:reddit:1r4jnof", app, bot)

    assert bot.borrados == []
    assert "umberto" not in yaml_path.read_text(encoding="utf-8")


async def test_los_votos_llegan_al_ranking() -> None:
    """Un boton que anota algo que nadie lee no serviria de nada."""
    from scrappy.ranking.scorer import Scorer

    assert "disliked_authors" in Scorer.__init__.__code__.co_varnames


# ---------------------------------------------------------------------------
# Casos raros
# ---------------------------------------------------------------------------
async def test_un_item_que_ya_no_esta_en_el_historial(settings: Settings) -> None:
    """Pasa con STATE_BACKEND=memory tras reiniciar."""
    app = AppFalsa(settings, None)

    query, _ = await _pulsar(f"{ACCION_BORRAR}:reddit:1r4jnof", app)

    texto, alerta = query.respuestas[0]
    assert "no encuentro ese item" in texto.lower()
    assert alerta is True


async def test_un_boton_mal_formado_no_rompe_nada(settings: Settings) -> None:
    app = AppFalsa(settings, _item())

    query, _ = await _pulsar(ACCION_BORRAR, app)  # sin fuente ni id

    assert "mal formado" in query.respuestas[0][0]


@pytest.mark.parametrize("fuente", ["reddit", "lemmy", "bluesky", "imgur", "giphy", "youtube", "x"])
def test_los_botones_caben_para_cualquier_fuente(fuente: str) -> None:
    """`callback_data` no admite mas de 64 bytes, y el nombre cuenta."""
    teclado = acciones_de_publicacion(fuente, "a" * 24)
    assert teclado is not None, f"no caben los botones para {fuente}"
    for boton in teclado.inline_keyboard[0]:
        assert len(str(boton.callback_data).encode()) <= 64
