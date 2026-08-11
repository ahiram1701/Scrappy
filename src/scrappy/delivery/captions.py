"""Construccion del texto que acompana a cada publicacion.

La atribucion no es configurable, y conviene explicar por que: este bot
republica trabajo de otras personas. Lo minimo exigible es decir quien lo hizo
y enlazar al original, para que el credito y el trafico vayan a su autor. No
hay ninguna opcion para desactivarlo. Ver `docs/LEGAL.md`.

El caption se genera en HTML (el modo de formato de Telegram mas predecible con
texto arbitrario de terceros) y se recorta al limite de 1024 caracteres que
impone la Bot API para medios.
"""

from __future__ import annotations

import html

from scrappy.config.loader import DeliveryConfig
from scrappy.core.models import RawCandidate, ScoredCandidate

# Limite de la Bot API para el caption de un medio. El de un mensaje de texto
# suelto es 4096, pero aqui siempre va adjunto a un medio.
CAPTION_LIMIT = 1024

_SOURCE_BADGES = {
    "reddit": "👽",
    "x": "𝕏",
    "tiktok": "🎵",
    "instagram": "📸",
}


def _escape(text: str) -> str:
    """Escapa texto que va como contenido de un elemento.

    Los titulos vienen de internet y pueden contener `<`, `&` o HTML
    intencionado. Con `<` y `&` escapados no hay forma de que se forme una
    etiqueta, asi que las comillas son inofensivas aqui.
    """
    return html.escape(text, quote=False)


def _escape_attr(value: str) -> str:
    """Escapa un valor que va DENTRO de un atributo `href="..."`.

    Aqui las comillas si importan: una URL de autor que contenga `"` podria
    cerrar el atributo antes de tiempo e inyectar los suyos. Como las URLs las
    proporciona la plataforma de origen, son entrada no confiable.
    """
    return html.escape(value, quote=True)


def _truncate(text: str, limit: int) -> str:
    """Recorta por palabras para no cortar a mitad de una."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "…"


def build_caption(
    scored: ScoredCandidate,
    config: DeliveryConfig,
    *,
    limit: int = CAPTION_LIMIT,
) -> str:
    """Genera el caption HTML de una publicacion.

    Estructura:

        <b>Titulo del post</b>

        👽 por <a href="...">autor</a> · <a href="...">ver original</a>

    La linea de atribucion se construye primero y el titulo se recorta a lo que
    sobre: si algo tiene que ceder espacio es el titulo, nunca el credito.
    """
    candidate = scored.candidate

    attribution = _build_attribution(candidate, config)
    if config.show_score:
        attribution += f" · <code>{scored.score:.3f}</code>"

    # Lo que sobra para el titulo: el limite menos la atribucion, menos los dos
    # saltos de linea, menos las etiquetas `<b></b>` que lo envuelven.
    overhead = len("\n\n") + len("<b></b>")
    available = limit - len(attribution) - overhead

    title = _escape(candidate.title.strip())
    if not title or available <= 0:
        return attribution

    return f"<b>{_truncate(title, available)}</b>\n\n{attribution}"


def _build_attribution(candidate: RawCandidate, config: DeliveryConfig) -> str:
    """Linea de credito. Siempre incluye autor y enlace al original."""
    parts: list[str] = []

    if config.show_source_badge:
        badge = _SOURCE_BADGES.get(candidate.source, "🔗")
        parts.append(badge)

    author = _escape(candidate.author)
    if candidate.author_url:
        parts.append(f'por <a href="{_escape_attr(candidate.author_url)}">{author}</a>')
    else:
        parts.append(f"por {author}")

    parts.append(f'· <a href="{_escape_attr(candidate.permalink)}">ver original</a>')

    return " ".join(parts)
