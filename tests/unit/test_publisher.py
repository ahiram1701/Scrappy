"""Pruebas del publisher.

No se habla con Telegram: se sustituye el `Bot` por un doble que apunta con que
metodo y con que argumentos se le llamo. Lo que importa verificar es que cada
tipo de medio use el metodo correcto de la Bot API (un video enviado como
documento pierde el reproductor), que los errores se traduzcan a mensajes
utiles, y que no queden handles de fichero abiertos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter

from scrappy.config.loader import DeliveryConfig
from scrappy.core.errors import PublishError
from scrappy.core.models import EphemeralMedia, MediaKind
from scrappy.delivery.publisher import TelegramPublisher
from tests.conftest import make_candidate, make_scored


class FakeMedia:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class FakeMessage:
    def __init__(self, message_id: int = 77, **media: Any) -> None:
        self.message_id = message_id
        self.video = media.get("video")
        self.animation = media.get("animation")
        self.photo = media.get("photo")
        self.document = media.get("document")


class FakeBot:
    """Bot de mentira que registra las llamadas y puede fallar a demanda."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._raises = raises

    def _record(self, method: str, kwargs: dict[str, Any]) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append((method, kwargs))

    async def send_photo(self, **kwargs: Any) -> FakeMessage:
        self._record("send_photo", kwargs)
        return FakeMessage(photo=[FakeMedia("small"), FakeMedia("grande")])

    async def send_video(self, **kwargs: Any) -> FakeMessage:
        self._record("send_video", kwargs)
        return FakeMessage(video=FakeMedia("video-id"))

    async def send_animation(self, **kwargs: Any) -> FakeMessage:
        self._record("send_animation", kwargs)
        return FakeMessage(animation=FakeMedia("anim-id"))

    async def send_message(self, **kwargs: Any) -> FakeMessage:
        self._record("send_message", kwargs)
        return FakeMessage()


def _publisher(bot: FakeBot, **config: Any) -> TelegramPublisher:
    # `delay_between_posts=0` para que los tests no esperen de verdad.
    defaults = {"delay_between_posts": 0.0}
    defaults.update(config)
    return TelegramPublisher(bot, "-100999", DeliveryConfig(**defaults))  # type: ignore[arg-type]


def _media(kind: MediaKind, *, path: Path | None = None) -> EphemeralMedia:
    common = {
        "candidate": make_candidate(kind=kind),
        "kind": kind,
        "filename": f"prueba.{'jpg' if kind is MediaKind.PHOTO else 'mp4'}",
        "sha256": "a" * 64,
        "duration_seconds": 12.7,
        "width": 720,
        "height": 1280,
    }
    if path is not None:
        return EphemeralMedia(path=path, size_bytes=path.stat().st_size, **common)  # type: ignore[arg-type]
    return EphemeralMedia(data=b"bytes-de-prueba", size_bytes=15, **common)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Metodo correcto por tipo de medio
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "metodo"),
    [
        (MediaKind.PHOTO, "send_photo"),
        (MediaKind.VIDEO, "send_video"),
        (MediaKind.ANIMATION, "send_animation"),
    ],
)
async def test_cada_tipo_usa_su_metodo(kind: MediaKind, metodo: str) -> None:
    bot = FakeBot()
    await _publisher(bot).publish(make_scored(), _media(kind))
    assert [name for name, _ in bot.calls] == [metodo]


async def test_el_video_se_envia_como_streaming() -> None:
    """Sin `supports_streaming` hay que bajar el clip entero antes de verlo."""
    bot = FakeBot()
    await _publisher(bot).publish(make_scored(), _media(MediaKind.VIDEO))

    _, kwargs = bot.calls[0]
    assert kwargs["supports_streaming"] is True
    assert kwargs["duration"] == 12
    assert kwargs["width"] == 720


async def test_devuelve_el_file_id_para_poder_reenviar() -> None:
    """El `file_id` es lo unico que queda del medio: Telegram es el archivo."""
    item = await _publisher(FakeBot()).publish(make_scored(), _media(MediaKind.VIDEO))

    assert item.telegram_file_id == "video-id"
    assert item.telegram_message_id == 77


async def test_de_una_foto_se_guarda_la_resolucion_mayor() -> None:
    item = await _publisher(FakeBot()).publish(make_scored(), _media(MediaKind.PHOTO))
    assert item.telegram_file_id == "grande"


async def test_el_item_publicado_conserva_los_hashes() -> None:
    media = _media(MediaKind.VIDEO)
    media.phash = "abcd1234abcd1234"

    item = await _publisher(FakeBot()).publish(make_scored(score=0.77), media)

    assert item.sha256 == "a" * 64
    assert item.phash == "abcd1234abcd1234"
    assert item.score == 0.77
    assert item.source == "reddit"


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------
async def test_el_caption_lleva_siempre_la_atribucion() -> None:
    bot = FakeBot()
    scored = make_scored(make_candidate(author="pepita"))
    await _publisher(bot).publish(scored, _media(MediaKind.PHOTO))

    _, kwargs = bot.calls[0]
    assert "pepita" in kwargs["caption"]
    assert scored.candidate.permalink in kwargs["caption"]


async def test_las_notificaciones_silenciosas_se_respetan() -> None:
    bot = FakeBot()
    await _publisher(bot, silent_notifications=True).publish(make_scored(), _media(MediaKind.PHOTO))
    assert bot.calls[0][1]["disable_notification"] is True


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
async def test_sin_permisos_en_el_canal_lo_explica() -> None:
    bot = FakeBot(raises=Forbidden("bot was kicked"))
    with pytest.raises(PublishError, match="administrador"):
        await _publisher(bot).publish(make_scored(), _media(MediaKind.PHOTO))


async def test_medio_rechazado() -> None:
    bot = FakeBot(raises=BadRequest("wrong file identifier"))
    with pytest.raises(PublishError, match="rechazo el medio"):
        await _publisher(bot).publish(make_scored(), _media(MediaKind.PHOTO))


async def test_rate_limit_no_se_reintenta_aqui() -> None:
    """AIORateLimiter ya reintenta; si llega hasta aqui, no vale insistir."""
    bot = FakeBot(raises=RetryAfter(30))
    with pytest.raises(PublishError, match="esperar"):
        await _publisher(bot).publish(make_scored(), _media(MediaKind.PHOTO))


# ---------------------------------------------------------------------------
# La garantia, en el ultimo eslabon
# ---------------------------------------------------------------------------
async def test_publicar_desde_fichero_no_deja_el_handle_abierto(tmp_path: Path) -> None:
    """Un handle abierto impediria borrar el workspace en Windows.

    Si el publisher dejase el fichero abierto, este `unlink` fallaria con
    PermissionError, que es exactamente el fallo silencioso que dejaria el
    video en el disco.
    """
    fichero = tmp_path / "video.mp4"
    fichero.write_bytes(b"x" * 4096)

    await _publisher(FakeBot()).publish(make_scored(), _media(MediaKind.VIDEO, path=fichero))

    fichero.unlink()
    assert not fichero.exists()


async def test_send_text() -> None:
    bot = FakeBot()
    await _publisher(bot).send_text("aviso operativo")
    assert bot.calls[0][0] == "send_message"
