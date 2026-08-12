"""Adapter de Reddit sobre los feeds Atom publicos.

## Por que RSS y no la API

Hasta 2025 lo correcto era usar la API oficial con OAuth. Ya no se puede:

- El registro autoservicio de apps en `/prefs/apps` **se cerro en noviembre de
  2025**. Ahora hay un formulario de aprobacion manual bajo la *Responsible
  Builder Policy*, y los proyectos personales se rechazan de forma sistematica.
- Los endpoints `.json` sin autenticar **se bloquearon el 28 de mayo de 2026**:
  devuelven 403.

Lo que sigue abierto son los feeds Atom, que Reddit nunca metio en la superficie
de pago. Este adapter los usa como estan pensados para usarse: un cliente que se
identifica honestamente, respeta los 429 y espacia sus peticiones.

Conviene saber que Reddit ha dado a entender que RSS podria ser lo siguiente que
cierre. Si un dia deja de funcionar, no sera un fallo de Scrappy.

## Lo que el feed da y lo que no

Da autor, id, permalink, titulo, fecha, miniatura y —dentro del `<content>`—
el enlace directo al medio.

**No da upvotes ni comentarios.** Pero viene ordenado por el listado `hot` del
subreddit —la mezcla de votos y antiguedad que hace el propio Reddit— asi que la
posicion es la senal. Encaja incluso mejor que los upvotes brutos en el modelo
de percentiles del scorer, porque la posicion ya *es* un percentil.

Ojo: el orden **no se puede cambiar**. El feed ignora `sort` y `t`; se comprobo
mandandolos y comparando los ids devueltos. Ver `docs/adr/0009-reddit-por-rss.md`.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from xml.etree import ElementTree

import httpx

from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus

_FEED_URL = "https://www.reddit.com/r/{subreddit}/.rss"

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

# El `<content>` del feed es una tabla HTML con dos enlaces: uno al medio,
# rotulado [link], y otro a los comentarios. Solo interesa el primero.
_MEDIA_LINK = re.compile(r'href="([^"]+)"[^>]*>\s*\[link\]', re.I)

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Dominios de video que yt-dlp resuelve bien.
_VIDEO_DOMAINS = (
    "v.redd.it",
    "redgifs.com",
    "streamable.com",
    "gfycat.com",
    "youtube.com",
    "youtu.be",
)

# Sin OAuth el limite ronda las 10 peticiones por minuto, y en la practica
# saltan 429 incluso espaciando 7 segundos. De ahi los 12 por defecto y, sobre
# todo, la rotacion de subreddits: es mejor consultar pocos por ronda que
# comerse un 429 a mitad de lista.
_DEFAULT_DELAY_SECONDS = 12.0
_DEFAULT_SUBS_PER_RUN = 3


class RedditSource(SourceAdapter):
    """Descubre posts leyendo los feeds Atom de los subreddits configurados."""

    name = "reddit"
    # RSS es una funcionalidad publica que Reddit sirve deliberadamente y este
    # adapter la consume como un lector de feeds educado. No es equiparable al
    # scraping de TikTok o Instagram, asi que no va detras del flag de ToS.
    requires_tos_ack = False

    async def status(self) -> SourceStatus:
        subreddits = self.config.get_list("subreddits")
        if not subreddits:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.reddit_enabled,
                configured=False,
                detail="no hay subreddits en config/sources.yaml",
            )
        if "/u/" not in self.settings.reddit_user_agent:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.reddit_enabled,
                configured=False,
                detail=(
                    "SCRAPPY_REDDIT_USER_AGENT debe identificarte, con el formato "
                    "'windows:scrappy:0.1.0 (by /u/tu_usuario)'; con uno generico "
                    "Reddit responde 429"
                ),
            )
        per_run = self.config.get_int("subreddits_per_run", _DEFAULT_SUBS_PER_RUN)
        return SourceStatus(
            name=self.name,
            enabled=self.settings.reddit_enabled,
            configured=True,
            detail=(f"{len(subreddits)} subreddits, {per_run} por ronda (feeds RSS, orden `hot`)"),
        )

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        subreddits = self.config.get_list("subreddits")
        if not subreddits:
            return []

        selection = self._rotate(subreddits)
        delay = float(self.config.get_int("delay_seconds", int(_DEFAULT_DELAY_SECONDS)))

        candidates: list[RawCandidate] = []
        for index, subreddit in enumerate(selection):
            if index:
                # Espaciado entre subreddits. Sin esto, el segundo o el tercero
                # se come un 429 casi seguro.
                await asyncio.sleep(delay)
            try:
                entries = await self._fetch_feed(subreddit)
            except RateLimitedError:
                # Si nos limitan, insistir con el resto solo empeora las cosas.
                self.log.warning("rate_limited_stop", subreddit=subreddit)
                break
            except SourceError as exc:
                self.log.warning("subreddit_failed", subreddit=subreddit, error=str(exc))
                continue

            candidates.extend(self._to_candidates(entries, subreddit))

        self.log.info(
            "discovered",
            count=len(candidates),
            subreddits=len(selection),
            de=len(subreddits),
        )
        return candidates[:budget] if budget else candidates

    def _rotate(self, subreddits: list[str]) -> list[str]:
        """Elige que subreddits tocan esta ronda.

        El rate limit no permite consultarlos todos cada vez, asi que se recorre
        la lista por tramos. El desplazamiento sale de la hora actual, de modo
        que rota solo entre ejecuciones sin necesidad de guardar estado: con 9
        subreddits y 3 por ronda, se cubre la lista entera cada tres rondas.
        """
        per_run = max(self.config.get_int("subreddits_per_run", _DEFAULT_SUBS_PER_RUN), 1)
        if per_run >= len(subreddits):
            return subreddits

        blocks = max(len(subreddits) // per_run, 1)
        offset = (int(time.time() // 3600) % blocks) * per_run
        rotated = subreddits[offset:] + subreddits[:offset]
        return rotated[:per_run]

    async def _fetch_feed(self, subreddit: str) -> list[ElementTree.Element]:
        """Descarga y parsea el feed Atom de un subreddit.

        **No se envia ningun parametro de orden, y es deliberado.** Comprobado
        contra Reddit: `?sort=top&t=day` y `?sort=new` devuelven la secuencia de
        ids *identica*, asi que el feed ignora `sort` por completo. Tampoco es
        orden cronologico (las edades no crecen de forma monotona): lo que
        sirve es el listado `hot` por defecto del subreddit.

        Mandar parametros que el servidor ignora en silencio solo sirve para
        que el siguiente que lea esto crea que puede cambiar el orden.

        Que sea `hot` y no `top` del dia no rompe nada: `hot` es la mezcla de
        votos y antiguedad que hace el propio Reddit, y para detectar lo que se
        esta moviendo ahora es incluso mejor senal. Lo que si implica es que el
        feed incluye posts de varios dias, asi que conviene no apretar
        `SCRAPPY_MAX_AGE_HOURS` en exceso o se descartara buena parte.
        """
        try:
            response = await self.client.get(
                _FEED_URL.format(subreddit=subreddit),
                headers={
                    "User-Agent": self.settings.reddit_user_agent,
                    "Accept": "application/atom+xml, application/xml",
                },
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"r/{subreddit}: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise RateLimitedError(self.name, float(retry_after) if retry_after else None)
        if response.status_code == 403:
            raise SourceError(
                self.name,
                f"r/{subreddit}: 403. El subreddit es privado, o Reddit ha cerrado "
                "tambien los feeds RSS (ver docs/adr/0009-reddit-por-rss.md).",
            )
        if response.status_code == 404:
            raise SourceError(self.name, f"r/{subreddit} no existe")
        if response.status_code != 200:
            raise SourceError(self.name, f"r/{subreddit}: HTTP {response.status_code}")

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise SourceError(
                self.name, f"r/{subreddit}: la respuesta no es un feed valido ({exc})"
            ) from exc

        # No basta con que parsee. Cuando Reddit bloquea devuelve su pagina de
        # error con un 200 enganoso, y ese HTML puede parsear como XML sin
        # quejarse: el resultado seria cero candidatos en silencio, que es el
        # peor fallo posible porque parece que simplemente no habia nada.
        if root.tag != f"{_ATOM}feed":
            raise SourceError(
                self.name,
                f"r/{subreddit}: la respuesta no es un feed valido (raiz <{root.tag}>). "
                "Suele significar que Reddit esta bloqueando la peticion; "
                "ver docs/adr/0009-reddit-por-rss.md",
            )

        return list(root.findall(f"{_ATOM}entry"))

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _to_candidates(
        self, entries: list[ElementTree.Element], subreddit: str
    ) -> list[RawCandidate]:
        total = len(entries)
        candidates: list[RawCandidate] = []

        for position, entry in enumerate(entries):
            candidate = self._to_candidate(entry, subreddit, position, total)
            if candidate is not None:
                candidates.append(candidate)

        return self._demote_pinned(candidates, subreddit)

    def _demote_pinned(self, candidates: list[RawCandidate], subreddit: str) -> list[RawCandidate]:
        """Quita el engagement heredado de la posicion a los posts fijados.

        Los moderadores fijan anuncios arriba del subreddit y el feed los sirve
        en las primeras posiciones sin ninguna marca que los distinga. Como aqui
        la posicion ES el engagement, un anuncio fijado se llevaria la nota mas
        alta del lote.

        Se detectan por la edad: en un feed de `top` del dia todo ronda las
        mismas horas, asi que una entrada varias veces mas vieja que la mediana
        no esta ahi por su score, sino por estar clavada. Se le asigna el
        engagement minimo en vez de descartarla, porque de vez en cuando un post
        fijado es contenido legitimo.
        """
        if len(candidates) < 5:
            return candidates

        now = utcnow()
        ages = sorted(candidate.age_hours(now=now) for candidate in candidates)
        median_age = ages[len(ages) // 2]
        if median_age <= 0:
            return candidates

        threshold = median_age * 3
        adjusted: list[RawCandidate] = []
        for candidate in candidates:
            if candidate.age_hours(now=now) > threshold:
                self.log.debug(
                    "pinned_demoted",
                    subreddit=subreddit,
                    uid=candidate.uid,
                    age_hours=round(candidate.age_hours(now=now)),
                )
                adjusted.append(candidate.model_copy(update={"engagement": 1}))
            else:
                adjusted.append(candidate)
        return adjusted

    def _to_candidate(
        self,
        entry: ElementTree.Element,
        subreddit: str,
        position: int,
        total: int,
    ) -> RawCandidate | None:
        raw_id = _text(entry, f"{_ATOM}id")
        if not raw_id:
            return None
        # Los ids vienen con el prefijo de tipo: `t3_1r4jnof`.
        source_id = raw_id.split("_", 1)[-1]

        link_element = entry.find(f"{_ATOM}link")
        permalink = link_element.get("href", "") if link_element is not None else ""
        if not permalink:
            return None

        content = _text(entry, f"{_ATOM}content") or ""
        kind, media_url = self._classify(content)
        if kind is None:
            return None

        author_element = entry.find(f"{_ATOM}author")
        author = "desconocido"
        author_url: str | None = None
        if author_element is not None:
            # `removeprefix` y no `lstrip("/u/")`: lstrip quita *cualquiera* de
            # esos caracteres, asi que un autor llamado "umberto" acabaria
            # convertido en "mberto".
            raw_author = _text(author_element, f"{_ATOM}name") or "desconocido"
            author = raw_author.removeprefix("/u/")
            author_url = _text(author_element, f"{_ATOM}uri")

        thumbnail_element = entry.find(f"{_MEDIA}thumbnail")
        thumbnail = thumbnail_element.get("url") if thumbnail_element is not None else None

        return RawCandidate(
            source=self.name,
            source_id=source_id,
            permalink=permalink,
            title=_text(entry, f"{_ATOM}title") or "",
            author=author,
            author_url=author_url,
            kind=kind,
            media_url=media_url,
            thumbnail_url=thumbnail,
            duration_seconds=None,  # el feed no lo informa
            created_at=_parse_datetime(_text(entry, f"{_ATOM}published")),
            # El feed no trae upvotes, pero viene ordenado por score del dia:
            # la posicion ES la senal. Se invierte para que el primero puntue
            # mas alto, y el scorer la convierte a percentil igual que haria
            # con los upvotes reales.
            engagement=max(total - position, 1),
            comments=0,
            nsfw=False,  # los feeds publicos no incluyen contenido marcado NSFW
            language=None,
            extra={"subreddit": subreddit, "feed_position": position},
        )

    @staticmethod
    def _classify(content_html: str) -> tuple[MediaKind | None, str | None]:
        """Deduce el tipo de medio a partir del enlace `[link]` del contenido.

        Devuelve `(None, None)` para los posts sin medio aprovechable: texto,
        enlaces a articulos y galerias.
        """
        match = _MEDIA_LINK.search(content_html)
        if not match:
            return None, None

        url = match.group(1).replace("&amp;", "&")
        clean = url.lower().split("?")[0]

        if any(domain in clean for domain in _VIDEO_DOMAINS):
            # Con v.redd.it el audio va en una pista DASH aparte, asi que la URL
            # directa seria muda: se delega en yt-dlp.
            return MediaKind.VIDEO, None
        if clean.endswith(".gif"):
            return MediaKind.ANIMATION, url
        if clean.endswith((".gifv", ".mp4")):
            return MediaKind.ANIMATION, None
        if clean.endswith(_IMAGE_SUFFIXES):
            return MediaKind.PHOTO, url
        return None, None


# ---------------------------------------------------------------------------
# Utilidades de parseo
# ---------------------------------------------------------------------------
def _text(element: ElementTree.Element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _parse_datetime(value: str | None) -> datetime:
    """Fecha ISO-8601 del feed, tolerando el sufijo `Z`."""
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return utcnow()
