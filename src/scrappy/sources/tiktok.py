"""Adapter de TikTok.

TikTok no ofrece una API publica que permita descubrir contenido viral y
descargarlo, asi que la unica via es enumerar hashtags y perfiles con yt-dlp.
Eso incumple sus terminos de servicio, se rompe cada vez que TikTok cambia algo
por dentro, y el contenido sigue perteneciendo a quien lo creo.

Por eso la fuente esta bloqueada salvo que se active
`SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true` a conciencia, y por eso la atribucion al
autor original es obligatoria en cada publicacion. Ver `docs/LEGAL.md`.
"""

from __future__ import annotations

from typing import Any

from scrappy.sources.ytdlp_base import YtDlpSource


class TikTokSource(YtDlpSource):
    """Enumera hashtags y perfiles de TikTok."""

    name = "tiktok"
    platform_label = "TikTok"
    # Sin cookies funciona a ratos; con cookies funciona bastante mas. No se
    # exigen para no bloquear a quien solo quiere probar.
    cookies_required = False

    def collection_urls(self) -> list[str]:
        urls = [
            f"https://www.tiktok.com/tag/{tag.lstrip('#')}"
            for tag in self.config.get_list("hashtags")
        ]
        urls += [
            f"https://www.tiktok.com/@{account.lstrip('@')}"
            for account in self.config.get_list("accounts")
        ]
        return urls

    def permalink_for(self, entry: dict[str, Any]) -> str:
        url = entry.get("url") or entry.get("webpage_url")
        if url:
            return str(url)
        uploader = str(entry.get("uploader") or "").lstrip("@")
        return f"https://www.tiktok.com/@{uploader}/video/{entry.get('id')}"

    def minimum_engagement(self) -> int:
        # En TikTok el numero que llega con mas fiabilidad es el de
        # reproducciones, y su escala es un orden de magnitud mayor que la de
        # likes de otras plataformas: sin un suelo alto entra demasiado ruido.
        return self.config.get_int("min_play_count", 0)
