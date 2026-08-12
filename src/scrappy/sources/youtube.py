"""Adapter de YouTube Shorts.

Usa la busqueda propia de yt-dlp (`ytsearchN:consulta`), que no necesita clave
de API. Tambien acepta pestanas `/shorts` de canales concretos.

## Por que va detras del flag de ToS

Cuando te propuse esta fuente la presente como si no tuviera el problema de
terminos de servicio de TikTok o Instagram. No es exacto: **descargar videos de
YouTube tambien incumple sus terminos**. Lo que si es cierto es que el perfil de
riesgo practico es mucho menor —no hacen falta cookies, no hay una cuenta tuya
que puedan limitar— pero el criterio del proyecto es "si la plataforma no ofrece
una via para este uso, va detras del flag", y YouTube no la ofrece.

Asi que `requires_tos_ack` se hereda como `True`. Ver `docs/LEGAL.md`.

## Que se considera un Short

YouTube ya admite Shorts de hasta 3 minutos, asi que no basta con el limite de
60 segundos de antano. Se filtra por `max_duration_seconds`, configurable, y se
descartan los directos y los estrenos, que nunca son contenido corto.
"""

from __future__ import annotations

from typing import Any

from scrappy.sources.ytdlp_base import YtDlpSource

# Duracion maxima de un Short en la plataforma. Por encima de esto es un video
# normal que se colo en los resultados.
_DEFAULT_MAX_DURATION = 180


class YouTubeSource(YtDlpSource):
    """Descubre Shorts por busqueda o por la pestana `/shorts` de un canal."""

    name = "youtube"
    platform_label = "YouTube"
    cookies_required = False

    def collection_urls(self) -> list[str]:
        urls: list[str] = []

        # `ytsearchN:` es la busqueda interna de yt-dlp: no necesita clave de
        # API ni depende de que los parametros de la URL de resultados sigan
        # significando lo mismo la semana que viene.
        per_query = max(self.config.get_int("results_per_query", 20), 1)
        for query in self.config.get_list("queries"):
            urls.append(f"ytsearch{per_query}:{query}")

        urls += [
            f"https://www.youtube.com/@{channel.lstrip('@')}/shorts"
            for channel in self.config.get_list("channels")
        ]
        return urls

    def permalink_for(self, entry: dict[str, Any]) -> str:
        url = entry.get("url") or entry.get("webpage_url")
        if url and str(url).startswith("http"):
            return str(url)
        return f"https://www.youtube.com/watch?v={entry.get('id')}"

    def minimum_engagement(self) -> int:
        return self.config.get_int("min_view_count", 0)

    def to_candidate(self, entry: dict[str, Any]) -> Any:
        """Anade a la normalizacion comun los descartes propios de YouTube."""
        # Los directos y los estrenos no son contenido corto y ademas no se
        # pueden descargar de forma fiable mientras estan en emision.
        if entry.get("is_live") or entry.get("live_status") in {"is_live", "is_upcoming"}:
            return None

        candidate = super().to_candidate(entry)
        if candidate is None:
            return None

        # YouTube devuelve videos largos en las busquedas aunque se pidan
        # Shorts. Con la duracion conocida se descartan aqui, antes de que
        # lleguen al ranking a competir por un hueco.
        max_duration = float(self.config.get_int("max_duration", _DEFAULT_MAX_DURATION))
        if candidate.duration_seconds and candidate.duration_seconds > max_duration:
            return None

        return candidate
