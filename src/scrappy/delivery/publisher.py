"""Publicacion del medio en Telegram.

Este es el unico punto del sistema donde el contenido sale del proceso, y es
tambien donde se obtiene lo unico que se conserva de el: el `file_id` que
devuelve Telegram, con el que se puede reenviar el medio mas adelante sin
volver a descargarlo de la plataforma original.

Los bytes se pasan como `InputFile` desde memoria o desde el fichero temporal.
En ningun caso se copia el medio a otra ubicacion.
"""

from __future__ import annotations

from typing import Any

from telegram import Bot, InputFile, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from scrappy.config.loader import DeliveryConfig
from scrappy.core.errors import PublishError
from scrappy.core.models import EphemeralMedia, MediaKind, PublishedItem, ScoredCandidate
from scrappy.delivery.captions import build_caption
from scrappy.delivery.throttle import PublishThrottle
from scrappy.observability.logging import get_logger

log = get_logger(__name__)


class TelegramPublisher:
    """Envia medios al chat destino y devuelve el rastro que queda de ellos."""

    def __init__(
        self,
        bot: Bot,
        chat_id: str,
        config: DeliveryConfig,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._config = config
        self._throttle = PublishThrottle(config.delay_between_posts)

    async def publish(self, scored: ScoredCandidate, media: EphemeralMedia) -> PublishedItem:
        """Publica un medio y devuelve su `PublishedItem`.

        Raises:
            PublishError: Telegram rechazo el envio.
        """
        await self._throttle.wait()

        caption = build_caption(scored, self._config)
        bound = log.bind(uid=scored.candidate.uid, kind=str(media.kind))

        try:
            message = await self._send(media, caption)
        except RetryAfter as exc:
            # AIORateLimiter reintenta por su cuenta; si aun asi llega aqui es
            # que el limite es serio y no merece la pena insistir en este item.
            raise PublishError(
                f"Telegram pide esperar {exc.retry_after}s antes de volver a publicar"
            ) from exc
        except Forbidden as exc:
            raise PublishError(
                "el bot no puede escribir en el chat destino. Anadelo al canal como "
                "administrador y comprueba SCRAPPY_TELEGRAM_TARGET_CHAT_ID."
            ) from exc
        except BadRequest as exc:
            raise PublishError(f"Telegram rechazo el medio: {exc}") from exc
        except TelegramError as exc:
            raise PublishError(f"fallo de Telegram: {exc}") from exc

        file_id = _extract_file_id(message)
        bound.info("published", message_id=message.message_id, size=media.size_bytes)

        return PublishedItem(
            source=scored.candidate.source,
            source_id=scored.candidate.source_id,
            permalink=scored.candidate.permalink,
            sha256=media.sha256,
            phash=media.phash,
            score=scored.score,
            kind=media.kind,
            telegram_message_id=message.message_id,
            telegram_file_id=file_id,
        )

    # ------------------------------------------------------------------
    # Envio
    # ------------------------------------------------------------------
    async def _send(self, media: EphemeralMedia, caption: str) -> Message:
        """Elige el metodo de la Bot API segun el tipo de medio.

        Importa: enviar un video como `send_document` lo deja sin reproductor,
        y enviar un GIF como `send_video` lo muestra con controles de audio que
        no existen. Cada tipo tiene su metodo.
        """
        payload = self._input_file(media)
        common: dict[str, Any] = {
            "chat_id": self._chat_id,
            "caption": caption,
            "parse_mode": ParseMode.HTML,
            "disable_notification": self._config.silent_notifications,
        }

        match media.kind:
            case MediaKind.PHOTO:
                return await self._bot.send_photo(photo=payload, **common)
            case MediaKind.ANIMATION:
                return await self._bot.send_animation(
                    animation=payload,
                    duration=_as_int(media.duration_seconds),
                    width=media.width,
                    height=media.height,
                    **common,
                )
            case MediaKind.VIDEO:
                return await self._bot.send_video(
                    video=payload,
                    duration=_as_int(media.duration_seconds),
                    width=media.width,
                    height=media.height,
                    # Permite empezar a ver sin descargar el fichero entero.
                    supports_streaming=True,
                    **common,
                )

    @staticmethod
    def _input_file(media: EphemeralMedia) -> InputFile:
        """Envuelve el medio venga de memoria o del workspace efimero.

        Se leen los bytes en vez de pasar un fichero abierto, y no es un
        detalle menor: `InputFile` lee el stream entero pero no lo cierra, asi
        que un handle abierto quedaria vivo hasta el siguiente paso del
        recolector. En Windows ese handle impide borrar el directorio, que es
        exactamente lo que este proyecto no se puede permitir. Como el limite
        de subida son 50 MB, leerlo entero no cuesta nada.
        """
        return InputFile(media.read_bytes(), filename=media.filename)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    async def send_text(self, text: str) -> Message:
        """Mensaje de texto suelto, para avisos operativos en el canal."""
        try:
            return await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_notification=True,
            )
        except TelegramError as exc:
            raise PublishError(f"no se pudo enviar el mensaje: {exc}") from exc


def _extract_file_id(message: Message) -> str | None:
    """Saca el `file_id` del medio que Telegram acaba de guardar.

    Con el se puede reenviar el mismo medio a otro chat sin volver a
    descargarlo: Telegram es, a efectos practicos, nuestro almacenamiento.
    """
    if message.video:
        return message.video.file_id
    if message.animation:
        return message.animation.file_id
    if message.photo:
        # `photo` es una lista de tamanos; el ultimo es el de mayor resolucion.
        return message.photo[-1].file_id
    if message.document:
        return message.document.file_id
    return None


def _as_int(value: float | None) -> int | None:
    return int(value) if value else None
