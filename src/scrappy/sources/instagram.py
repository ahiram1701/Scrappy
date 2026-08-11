"""Adapter de Instagram.

Instagram es la plataforma mas restrictiva de las cuatro: practicamente todo
requiere una sesion iniciada, de modo que aqui las cookies no son opcionales.
Usar una cuenta propia para esto puede acabar con esa cuenta limitada o
bloqueada, ademas de incumplir los terminos de Meta.

Como las demas fuentes de riesgo, esta desactivada salvo consentimiento
explicito y publica siempre con atribucion al autor. Ver `docs/LEGAL.md`.
"""

from __future__ import annotations

from typing import Any

from scrappy.sources.ytdlp_base import YtDlpSource


class InstagramSource(YtDlpSource):
    """Enumera hashtags y perfiles de Instagram."""

    name = "instagram"
    platform_label = "Instagram"
    cookies_required = True

    def collection_urls(self) -> list[str]:
        urls = [
            f"https://www.instagram.com/explore/tags/{tag.lstrip('#')}/"
            for tag in self.config.get_list("hashtags")
        ]
        urls += [
            f"https://www.instagram.com/{account.lstrip('@')}/"
            for account in self.config.get_list("accounts")
        ]
        return urls

    def permalink_for(self, entry: dict[str, Any]) -> str:
        url = entry.get("url") or entry.get("webpage_url")
        if url:
            return str(url)
        return f"https://www.instagram.com/p/{entry.get('id')}/"
