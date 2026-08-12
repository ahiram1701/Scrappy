"""Adapter de Giphy.

Giphy dejo de ser gratis del todo, pero la clave *beta* sigue siendo
autoservicio en https://developers.giphy.com y da 100 llamadas por hora. Para
un bot que publica cada tres horas eso sobra de largo: cada ronda gasta una
llamada por consulta.

## La particularidad: aqui no hay engagement

La API de Giphy **no expone likes, vistas ni ningun contador**. Lo unico que
hay es el propio orden de `trending`, que ya es un ranking hecho por ellos.

Se usa exactamente igual que en Reddit por RSS: la posicion es la senal, y el
scorer la convierte a percentil. En este caso es incluso mas defendible, porque
el orden de `trending` ES la medida de popularidad de Giphy, no una
aproximacion.

NOTA: como Imgur, este adapter no se ha podido verificar contra la API real
porque hace falta una clave. Esta construido sobre el formato documentado de la
API v1 y probado con fixtures.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

_API = "https://api.giphy.com/v1/gifs"
_DEFAULT_DELAY_SECONDS = 1.0

# Clasificacion de contenido de Giphy. `pg-13` deja fuera lo subido de tono sin
# quedarse solo con contenido infantil.
_DEFAULT_RATING = "pg-13"

_MAX_LIMIT = 50


class GiphySource(SourceAdapter):
    """Descubre GIFs en las tendencias y en las busquedas configuradas."""

    name = "giphy"
    requires_tos_ack = False

    async def status(self) -> SourceStatus:
        if not self.settings.giphy_api_key.get_secret_value():
            return SourceStatus(
                name=self.name,
                enabled=self.settings.giphy_enabled,
                configured=False,
                detail=(
                    "falta SCRAPPY_GIPHY_API_KEY. La clave beta es autoservicio en "
                    "https://developers.giphy.com y da 100 llamadas/hora, de sobra "
                    "para este uso"
                ),
            )
        queries = self.config.get_list("queries")
        detalle = f"tendencias + {len(queries)} busquedas" if queries else "tendencias"
        return SourceStatus(
            name=self.name,
            enabled=self.settings.giphy_enabled,
            configured=True,
            detail=f"{detalle} (clave beta: 100 llamadas/hora)",
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        queries = self.config.get_list("queries")
        delay = float(self.config.get_int("delay_seconds", int(_DEFAULT_DELAY_SECONDS)))
        rating = self.config.get_str("rating", _DEFAULT_RATING)

        # `None` representa las tendencias, que siempre se consultan.
        targets: list[str | None] = [None, *queries]
        per_target = min(max(budget // len(targets), 10), _MAX_LIMIT)

        candidates: list[RawCandidate] = []
        for index, query in enumerate(targets):
            if index:
                await asyncio.sleep(delay)
            try:
                items = await self._fetch(query, per_target, rating)
            except RateLimitedError:
                self.log.warning("rate_limited_stop", query=query)
                break
            except SourceError as exc:
                self.log.warning("query_failed", query=query, error=str(exc))
                continue

            candidates.extend(self._to_candidates(items))

        self.log.info("discovered", count=len(candidates), targets=len(targets))
        return candidates

    async def _fetch(self, query: str | None, limit: int, rating: str) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "api_key": self.settings.giphy_api_key.get_secret_value(),
            "limit": limit,
            "rating": rating,
        }
        endpoint = "/trending"
        if query:
            endpoint = "/search"
            params["q"] = query

        try:
            response = await self.client.get(f"{_API}{endpoint}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con Giphy: {exc}") from exc

        if response.status_code == 429:
            raise RateLimitedError(self.name)
        if response.status_code in (401, 403):
            raise SourceError(
                self.name,
                "clave rechazada. Comprueba SCRAPPY_GIPHY_API_KEY; si la clave beta "
                "ha superado sus 100 llamadas/hora, espera o reduce las consultas.",
            )
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(self.name, "la respuesta no es JSON valido") from exc

        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidates(self, items: list[dict[str, Any]]) -> list[RawCandidate]:
        total = len(items)
        candidates: list[RawCandidate] = []
        for position, item in enumerate(items):
            candidate = self._to_candidate(item, position, total)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _to_candidate(self, item: dict[str, Any], position: int, total: int) -> RawCandidate | None:
        item_id = item.get("id")
        if not item_id:
            return None

        original = (item.get("images") or {}).get("original") or {}
        # Se prefiere el mp4: pesa una fraccion de lo que pesa el GIF y Telegram
        # lo reproduce mejor.
        media_url = original.get("mp4") or original.get("url")
        if not media_url:
            return None

        author = str(item.get("username") or "")

        return RawCandidate(
            source=self.name,
            source_id=str(item_id),
            permalink=str(item.get("url") or f"https://giphy.com/gifs/{item_id}"),
            title=str(item.get("title") or ""),
            author=author or "Giphy",
            author_url=f"https://giphy.com/{author}" if author else None,
            kind=MediaKind.ANIMATION,
            media_url=str(media_url),
            thumbnail_url=(item.get("images") or {}).get("fixed_width_small", {}).get("url"),
            duration_seconds=None,
            created_at=_parse_datetime(item.get("import_datetime")),
            # Giphy no expone ningun contador: el orden de `trending` es la
            # unica senal, y ya es un ranking hecho por ellos.
            engagement=max(total - position, 1),
            comments=0,
            nsfw=str(item.get("rating") or "").lower() in {"r", "nc-17"},
            language=None,
            extra={"rating": item.get("rating"), "trending_position": position},
        )


def _parse_datetime(value: Any) -> datetime:
    """Giphy usa `YYYY-MM-DD HH:MM:SS` en UTC, sin zona explicita."""
    if not isinstance(value, str) or not value.strip():
        return utcnow()
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return utcnow()
