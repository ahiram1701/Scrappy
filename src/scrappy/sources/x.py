"""Adapter de X (Twitter), con dos backends intercambiables.

Es la fuente con la situacion mas incomoda del proyecto, y conviene decirlo sin
rodeos:

    backend `api`     Usa la API oficial v2 (`/2/tweets/search/recent`). Es la
                      via limpia, pero el tier gratuito de X NO permite buscar
                      posts: el endpoint de busqueda requiere el tier Basic,
                      que en el momento de escribir esto ronda los 200 USD al
                      mes. Con el tier gratuito este backend devolvera 403.

    backend `scrape`  Pide los tweets a la GraphQL interna de la web de X con
                      las cookies de una sesion, y deja la descarga a yt-dlp.
                      No cuesta dinero, pero incumple los terminos de servicio
                      de X, se rompera cuando la plataforma cambie por dentro,
                      y puede acarrear el bloqueo de la cuenta cuyas cookies se
                      usen. Por eso deben salir de una **cuenta desechable**,
                      creada para esto: es lo unico que convierte «te bloquean
                      la cuenta» en un problema sin consecuencias.

                      Antes enumeraba perfiles con yt-dlp, que no puede: no
                      tiene extractor de timelines de X. Ver la correccion en
                      `docs/adr/0003-x-doble-backend.md`.

Se elige con `SCRAPPY_X_BACKEND`. El backend `scrape` ademas exige activar
`SCRAPPY_ENABLE_TOS_RISKY_SOURCES`. Ver `docs/LEGAL.md` y
`docs/adr/0003-x-doble-backend.md`.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from scrappy.config.settings import Settings, XBackend
from scrappy.core.errors import RateLimitedError, SourceError
from scrappy.core.models import MediaKind, RawCandidate, utcnow
from scrappy.sources.base import SourceAdapter, SourceStatus, rotar_objetivos
from scrappy.sources.x_cookies import (
    COOKIES_IMPRESCINDIBLES,
    cookies_del_fichero,
    renovar_cookies,
)

_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"

# ---------------------------------------------------------------------------
# Backend `scrape`
# ---------------------------------------------------------------------------
_GRAPHQL_BASE = "https://x.com/i/api/graphql"

#: El bundle JS de la web, de donde se leen los `queryId` en caliente.
_BUNDLE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web[^\"']*?/main\.[a-f0-9]+\.js"
)
_QUERY_ID = re.compile(r'queryId:"([^"]+)",operationName:"([^"]+)"')

#: Enlaces `t.co` al final del texto: en un tweet con video es el enlace al
#: propio video, y en el titulo de la publicacion solo estorba.
_ENLACE_TCO = re.compile(r"\s*https://t\.co/\w+\s*$")

#: `features` que exige `UserByScreenName`. `UserMedia` no pide ninguna.
_FEATURES_DE_USUARIO = json.dumps(
    {
        "hidden_profile_subscriptions_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "subscriptions_verification_info_is_identity_verified_enabled": True,
        "subscriptions_verification_info_verified_since_enabled": True,
        "highlights_tweets_tab_ui_enabled": True,
        "responsive_web_twitter_article_notes_tab_enabled": True,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "responsive_web_graphql_timeline_navigation_enabled": True,
    }
)

#: User-Agent de navegador. Aqui no es un disfraz: la GraphQL de la web
#: responde 403 al User-Agent honesto de Scrappy, asi que sin esto no hay
#: fuente. Lo que si se mantiene es el ritmo, que es lo que de verdad importa.
_UA_NAVEGADOR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"


class SesionInvalidaError(SourceError):
    """La sesion de X ya no vale: cookies caducadas o revocadas.

    Tiene tipo propio porque es el unico fallo de esta fuente que Scrappy puede
    arreglar por su cuenta -reextrayendo del navegador- y hay que distinguirlo
    de un perfil borrado o de un fallo de red.
    """


def _bearer_publico() -> str:
    """El bearer de la web de X, que es publico y constante.

    Se toma de yt-dlp, que ya es dependencia del proyecto y lo mantiene al dia;
    si algun dia esa constante privada desaparece, queda el valor conocido.
    """
    try:
        from yt_dlp.extractor.twitter import TwitterBaseIE

        token = getattr(TwitterBaseIE, "_AUTH", "")
        if isinstance(token, str) and token:
            return token
    except ImportError:  # pragma: no cover - yt-dlp es dependencia dura
        pass
    return (
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
        "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
    )


_BEARER = _bearer_publico()

# Campos minimos para poder rankear y atribuir. Pedir de mas gasta cuota.
_TWEET_FIELDS = "created_at,public_metrics,lang,possibly_sensitive,attachments,author_id"
_EXPANSIONS = "author_id,attachments.media_keys"
_USER_FIELDS = "username,name"
_MEDIA_FIELDS = "type,duration_ms,preview_image_url,public_metrics"


class XApiSource(SourceAdapter):
    """Backend oficial: busqueda reciente de la API v2."""

    name = "x"
    requires_tos_ack = False  # la API oficial es un uso permitido... si se paga

    async def status(self) -> SourceStatus:
        token = self.settings.x_bearer_token.get_secret_value()
        if not token:
            return SourceStatus(
                name=self.name,
                enabled=self.settings.x_enabled,
                configured=False,
                detail="falta SCRAPPY_X_BEARER_TOKEN",
            )
        if not self.config.get_list("queries"):
            return SourceStatus(
                name=self.name,
                enabled=self.settings.x_enabled,
                configured=False,
                detail="no hay `queries` en config/sources.yaml",
            )
        return SourceStatus(
            name=self.name,
            enabled=self.settings.x_enabled,
            configured=True,
            detail="backend api (requiere tier Basic o superior para buscar)",
        )

    async def discover(self, budget: int) -> list[RawCandidate]:
        queries = self.config.get_list("queries")
        if not queries:
            return []

        per_query = max(budget // len(queries), 10)
        candidates: list[RawCandidate] = []

        for query in queries:
            try:
                payload = await self._search(query, per_query)
            except RateLimitedError:
                raise
            except SourceError as exc:
                self.log.warning("query_failed", query=query, error=str(exc))
                continue
            candidates.extend(self._parse(payload))

        self.log.info("discovered", count=len(candidates), queries=len(queries))
        return candidates

    async def _search(self, query: str, limit: int) -> dict[str, Any]:
        token = self.settings.x_bearer_token.get_secret_value()
        params: dict[str, str | int] = {
            "query": query,
            "max_results": min(max(limit, 10), 100),
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "media.fields": _MEDIA_FIELDS,
        }
        try:
            response = await self.client.get(
                _SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con la API de X: {exc}") from exc

        if response.status_code == 429:
            reset = response.headers.get("x-rate-limit-reset")
            retry_after: float | None = None
            if reset:
                with_suppress = utcnow().timestamp()
                retry_after = max(float(reset) - with_suppress, 0.0)
            raise RateLimitedError(self.name, retry_after)

        if response.status_code == 403:
            raise SourceError(
                self.name,
                "HTTP 403. El endpoint de busqueda de la API de X no esta incluido "
                "en el tier gratuito; hace falta el tier Basic o superior. "
                "Alternativa: SCRAPPY_X_BACKEND=scrape (lee docs/LEGAL.md antes).",
            )
        if response.status_code == 401:
            raise SourceError(self.name, "bearer token invalido o caducado")
        if response.status_code != 200:
            raise SourceError(self.name, f"HTTP {response.status_code}: {response.text[:200]}")

        return dict(response.json())

    def _parse(self, payload: dict[str, Any]) -> list[RawCandidate]:
        """Reconstruye los tweets uniendo `data` con las `includes` expandidas."""
        tweets = payload.get("data") or []
        includes = payload.get("includes") or {}
        users = {user["id"]: user for user in includes.get("users", [])}
        media = {item["media_key"]: item for item in includes.get("media", [])}

        candidates: list[RawCandidate] = []
        for tweet in tweets:
            media_keys = (tweet.get("attachments") or {}).get("media_keys") or []
            attached = [media[key] for key in media_keys if key in media]
            videos = [item for item in attached if item.get("type") in {"video", "animated_gif"}]
            if not videos:
                # La query pide has:videos, pero si la expansion no llego no se
                # puede saber que descargar. Mejor saltarlo que publicar texto.
                continue

            first = videos[0]
            user = users.get(tweet.get("author_id", ""), {})
            username = user.get("username", "desconocido")
            metrics = tweet.get("public_metrics") or {}

            duration_ms = first.get("duration_ms")
            candidates.append(
                RawCandidate(
                    source=self.name,
                    source_id=str(tweet["id"]),
                    permalink=f"https://x.com/{username}/status/{tweet['id']}",
                    title=str(tweet.get("text") or "")[:500],
                    author=username,
                    author_url=f"https://x.com/{username}",
                    kind=(
                        MediaKind.ANIMATION
                        if first.get("type") == "animated_gif"
                        else MediaKind.VIDEO
                    ),
                    media_url=None,
                    thumbnail_url=first.get("preview_image_url"),
                    duration_seconds=float(duration_ms) / 1000 if duration_ms else None,
                    created_at=_parse_iso(tweet.get("created_at")),
                    engagement=int(metrics.get("like_count", 0))
                    + int(metrics.get("retweet_count", 0)),
                    comments=int(metrics.get("reply_count", 0)),
                    nsfw=bool(tweet.get("possibly_sensitive")),
                    language=tweet.get("lang"),
                    extra={"backend": "api"},
                )
            )
        return candidates


class XScrapeSource(SourceAdapter):
    """Backend de scraping: descubre por la GraphQL interna de X.

    ## Por que ya no es yt-dlp

    Lo era, y no podia funcionar. yt-dlp **no tiene extractor para timelines de
    X**: solo para tweets sueltos (`/status/<id>`), cards, spaces y broadcasts.
    Enumerar `https://x.com/<cuenta>` devolvia `Unsupported URL` en cada ronda,
    con cookies o sin ellas. Ver `docs/adr/0003-x-doble-backend.md`.

    Como Scrappy separa **descubrir** de **descargar**, solo hacia falta
    sustituir la primera mitad: aqui se piden los tweets a la GraphQL que usa la
    propia web de X, y la descarga sigue siendo de yt-dlp contra la URL
    `/status/<id>`, que si sabe resolver. El descargador ya le pasa las cookies
    de esta fuente, asi que no hay nada que coordinar.

    ## Lo que esto es, dicho claro

    Llamar autenticado a una API privada es mas intrusivo que leer paginas
    publicas, no menos, y se rompera cuando X cambie algo por dentro. De ahi
    las tres decisiones de abajo:

    - **Nada que pueda pudrirse esta hardcodeado.** Los `queryId` se sacan del
      bundle JS de la propia web; si X los rota, esto se entera solo. Se pueden
      fijar a mano en el YAML por si algun dia deja de valer.
    - **El ritmo es el de siempre**: rotacion de cuentas, espera entre ellas y
      retirada en cuanto llega un 429. Lo que distingue a un lector de un
      scraper no es cuanto pide sino a que velocidad.
    - **Los ids de usuario se cachean.** No cambian nunca, asi que a partir de
      la segunda ronda cada cuenta cuesta una peticion en vez de dos.
    """

    name = "x"
    requires_tos_ack = True

    #: Espera por defecto entre cuentas. X no publica su limite; el numero sale
    #: de quedarse por debajo del ritmo al que alguien abriria dos perfiles.
    default_delay_seconds = 30.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ids: dict[str, str] = {}
        self._queries: dict[str, str] | None = None
        #: Si ya se renovo la sesion en esta ronda. Se renueva una sola vez.
        self._renovado = False

    # ------------------------------------------------------------------
    # Sesion
    # ------------------------------------------------------------------
    def _cookies(self) -> dict[str, str]:
        """Las cookies de X del fichero configurado, filtradas por dominio.

        La mecanica vive en `x_cookies` porque el diagnostico y la validacion de
        cuentas necesitan exactamente lo mismo.
        """
        return cookies_del_fichero(self.settings)

    def _headers(self, cookies: dict[str, str]) -> dict[str, str]:
        """Cabeceras que espera la GraphQL: el bearer publico y el csrf del `ct0`.

        Las cookies van en la cabecera y **no** en el tarro del cliente: ese
        cliente httpx lo comparten todas las fuentes, y la sesion de X no tiene
        por que vivir en un objeto por el que tambien pasan Reddit y Lemmy.
        """
        return {
            "Authorization": f"Bearer {_BEARER}",
            "x-csrf-token": cookies.get("ct0", ""),
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "User-Agent": _UA_NAVEGADOR,
            "Cookie": _cabecera_de_cookies(cookies),
        }

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    async def status(self) -> SourceStatus:
        enabled = self.settings.x_enabled

        if not self.settings.enable_tos_risky_sources:
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail="bloqueada: requiere SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true "
                "(lee docs/LEGAL.md)",
            )

        ruta = self.settings.cookies_file_for(self.name)
        if ruta is None:
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail="falta SCRAPPY_X_COOKIES_FILE (de una cuenta desechable)",
            )
        if not ruta.exists():
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail=f"el fichero de cookies {ruta} no existe",
            )

        cookies = self._cookies()
        faltan = [nombre for nombre in COOKIES_IMPRESCINDIBLES if not cookies.get(nombre)]
        if faltan:
            # Sin `ct0` no hay csrf y X responde 403 a todo; sin `auth_token` no
            # hay sesion. Decirlo aqui evita descubrirlo tres horas despues.
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail=f"las cookies no traen {' ni '.join(faltan)}; reexportalas",
            )

        cuentas = self.config.get_list("accounts")
        if not cuentas:
            return SourceStatus(
                name=self.name,
                enabled=enabled,
                configured=False,
                detail="no hay `accounts` en config/sources.yaml",
            )

        por_ronda = self.config.get_int("objetivos_por_ronda", len(cuentas))
        detalle = f"backend scrape, {len(cuentas)} cuentas"
        if por_ronda < len(cuentas):
            detalle += f", {max(por_ronda, 1)} por ronda"
        return SourceStatus(name=self.name, enabled=enabled, configured=True, detail=detalle)

    # ------------------------------------------------------------------
    # Descubrimiento
    # ------------------------------------------------------------------
    async def discover(self, budget: int) -> list[RawCandidate]:
        self.ensure_tos_acknowledged()

        cookies = self._cookies()
        if not cookies.get("auth_token") or not cookies.get("ct0"):
            # Sin sesion no hay nada que pedir, y gastar una peticion en
            # confirmarlo no ayuda. `status()` ya lo cuenta en `/sources`.
            self.log.warning("sin_sesion", detail="cookies ausentes o incompletas")
            return []

        todas = [cuenta.lstrip("@") for cuenta in self.config.get_list("accounts")]
        if not todas:
            return []

        cuentas = rotar_objetivos(todas, self.config.get_int("objetivos_por_ronda", len(todas)))
        delay = float(self.config.get_int("delay_seconds", int(self.default_delay_seconds)))
        por_cuenta = max(budget // len(cuentas), 5)

        candidatos: list[RawCandidate] = []
        self._renovado = False
        for indice, cuenta in enumerate(cuentas):
            if indice:
                await asyncio.sleep(delay)
            try:
                items, cookies = await self._medios_con_renovacion(cuenta, por_cuenta, cookies)
            except RateLimitedError:
                # Insistir con el resto de la lista solo confirma el patron que
                # nos ha delatado.
                self.log.warning("rate_limited_stop", cuenta=cuenta)
                break
            except SesionInvalidaError:
                # **Se propaga a proposito.** Una sesion muerta no es un problema
                # de esta cuenta sino de la fuente entera, asi que seguir con las
                # demas no arreglaria nada. Y sobre todo: dejandola subir queda
                # apuntada en los errores de la ronda, que es de donde el
                # scheduler saca el aviso a Telegram. Tragarsela en un log
                # significaria que nadie se entera nunca.
                raise
            except SourceError as exc:
                self.log.warning("cuenta_fallida", cuenta=cuenta, error=str(exc))
                continue
            candidatos.extend(self._a_candidatos(items, cuenta))

        self.log.info("discovered", count=len(candidatos), cuentas=len(cuentas), de=len(todas))
        return candidatos

    async def _medios_con_renovacion(
        self, cuenta: str, cuantos: int, cookies: dict[str, str]
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Los medios de una cuenta, renovando la sesion si hace falta.

        Devuelve tambien las cookies en uso, que cambian si hubo renovacion: el
        bucle tiene que seguir con las nuevas y no con las muertas.

        Se renueva **una vez por ronda**. Si vuelve a fallar despues de haber
        renovado, el error sube: reintentar en bucle contra quien acaba de
        rechazarnos es justo lo que este proyecto lleva evitando.
        """
        try:
            return await self._medios(cuenta, cuantos, cookies), cookies
        except SesionInvalidaError:
            if self._renovado:
                raise
            self._renovado = True
            cookies = self._renovar()
            return await self._medios(cuenta, cuantos, cookies), cookies

    def _renovar(self) -> dict[str, str]:
        """Reextrae la sesion del navegador y devuelve las cookies recargadas.

        Si no se puede -no hay navegador configurado, o tampoco tiene sesion-,
        se lanza `SesionInvalidaError` con el motivo real: el usuario tiene que
        leer que hace falta entrar a X a mano, no un «403» otra vez.
        """
        self.log.warning("sesion_invalida", detail="reextrayendo del navegador")
        resultado = renovar_cookies(self.settings)
        if not resultado.ok:
            raise SesionInvalidaError(self.name, resultado.detalle)

        cookies = self._cookies()
        if not cookies.get("auth_token") or not cookies.get("ct0"):
            raise SesionInvalidaError(self.name, "la sesion renovada sigue sin servir")

        self.log.info("sesion_renovada", de_x=resultado.de_x)
        return cookies

    async def _medios(
        self, cuenta: str, cuantos: int, cookies: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Los tweets con medios de un perfil, desenvueltos del timeline."""
        queries = await self._query_ids(cookies)
        user_id = await self._user_id(cuenta, cookies, queries)

        datos = await self._graphql(
            queries["UserMedia"],
            "UserMedia",
            {
                "userId": user_id,
                "count": min(max(cuantos, 5), 100),
                "includePromotedContent": False,
                "withClientEventToken": False,
                "withBirdwatchNotes": False,
                "withVoice": True,
                "withV2Timeline": True,
            },
            cookies,
        )
        try:
            timeline = datos["data"]["user"]["result"]["timeline"]["timeline"]
            instrucciones = timeline["instructions"]
        except (KeyError, TypeError) as exc:
            raise SourceError(self.name, f"@{cuenta}: la respuesta no trae timeline") from exc

        return [
            item
            for instruccion in instrucciones
            if instruccion.get("type") == "TimelineAddEntries"
            for entrada in instruccion.get("entries", [])
            for item in (entrada.get("content", {}).get("items") or [])
        ]

    async def _user_id(self, cuenta: str, cookies: dict[str, str], queries: dict[str, str]) -> str:
        """El id numerico de un handle. Se cachea: no cambia nunca."""
        if cuenta in self._ids:
            return self._ids[cuenta]

        datos = await self._graphql(
            queries["UserByScreenName"],
            "UserByScreenName",
            {"screen_name": cuenta, "withSafetyModeUserFields": True},
            cookies,
            features=_FEATURES_DE_USUARIO,
        )
        try:
            user_id = str(datos["data"]["user"]["result"]["rest_id"])
        except (KeyError, TypeError) as exc:
            raise SourceError(self.name, f"no existe la cuenta @{cuenta}") from exc

        self._ids[cuenta] = user_id
        return user_id

    async def _query_ids(self, cookies: dict[str, str]) -> dict[str, str]:
        """Los `queryId` de la GraphQL, leidos del bundle JS de la propia web.

        X los rota sin avisar, y hardcodearlos seria firmar que esto se rompa en
        una fecha desconocida. Leerlos cuesta dos peticiones por arranque y se
        entera solo de los cambios. Si algun dia el bundle cambia de forma, se
        pueden fijar en el YAML con `query_id_user` y `query_id_media`.

        La portada se pide **con la sesion**: sin cookies X sirve una pagina de
        aterrizaje de 35 KB que no referencia el bundle. Comprobado.
        """
        if self._queries is not None:
            return self._queries

        fijos = {
            "UserByScreenName": self.config.get_str("query_id_user", ""),
            "UserMedia": self.config.get_str("query_id_media", ""),
        }
        if all(fijos.values()):
            self._queries = fijos
            return fijos

        try:
            portada = await self.client.get(
                "https://x.com/",
                headers={"User-Agent": _UA_NAVEGADOR, "Cookie": _cabecera_de_cookies(cookies)},
                follow_redirects=True,
            )
            bundles = _BUNDLE.findall(portada.text)
            if not bundles:
                # X solo referencia el bundle en la portada de una sesion
                # iniciada; sin sesion sirve una pagina de aterrizaje de 35 KB.
                # Asi que esto NO es «no encontre el fichero»: es la primera
                # senal de que la sesion murio, y llega antes que ningun 401.
                raise SesionInvalidaError(
                    self.name,
                    "x.com no sirve la web de una sesion iniciada: la sesion no vale",
                )
            js = await self.client.get(bundles[0], headers={"User-Agent": _UA_NAVEGADOR})
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo leer el bundle de x.com: {exc}") from exc

        del_bundle = {op: qid for qid, op in _QUERY_ID.findall(js.text)}
        queries = {
            nombre: fijos[nombre] or del_bundle.get(nombre, "")
            for nombre in ("UserByScreenName", "UserMedia")
        }
        if not all(queries.values()):
            raise SourceError(
                self.name,
                "el bundle de x.com no trae los queryId esperados. Fijalos a mano en "
                "config/sources.yaml, con `query_id_user` y `query_id_media`.",
            )

        self.log.info("query_ids_descubiertos", **queries)
        self._queries = queries
        return queries

    async def _graphql(
        self,
        query_id: str,
        operacion: str,
        variables: dict[str, Any],
        cookies: dict[str, str],
        features: str = "{}",
    ) -> dict[str, Any]:
        """Una llamada a la GraphQL, traduciendo sus fallos a los de Scrappy."""
        try:
            respuesta = await self.client.get(
                f"{_GRAPHQL_BASE}/{query_id}/{operacion}",
                params={"variables": json.dumps(variables), "features": features},
                headers=self._headers(cookies),
            )
        except httpx.HTTPError as exc:
            raise SourceError(self.name, f"no se pudo contactar con X: {exc}") from exc

        if respuesta.status_code == 429:
            reset = respuesta.headers.get("x-rate-limit-reset")
            espera = max(float(reset) - utcnow().timestamp(), 0.0) if reset else None
            raise RateLimitedError(self.name, espera)
        if respuesta.status_code in (401, 403):
            raise SesionInvalidaError(
                self.name,
                f"HTTP {respuesta.status_code}: la sesion ya no vale. Renuevala con "
                "`scrappy cookies`, con el boton de /sources en Telegram, o desde la TUI.",
            )
        if respuesta.status_code != 200:
            raise SourceError(self.name, f"HTTP {respuesta.status_code}: {respuesta.text[:200]}")

        datos: dict[str, Any] = respuesta.json()
        if errores := datos.get("errors"):
            mensaje = "; ".join(str(e.get("message", e))[:120] for e in errores[:2])
            raise SourceError(self.name, f"{operacion}: {mensaje}")
        return datos

    # ------------------------------------------------------------------
    # Normalizacion
    # ------------------------------------------------------------------
    def _a_candidatos(self, items: list[dict[str, Any]], cuenta: str) -> list[RawCandidate]:
        candidatos: list[RawCandidate] = []
        for item in items:
            contenido = item.get("item", {}).get("itemContent", {})
            resultado = (contenido.get("tweet_results") or {}).get("result")
            if not isinstance(resultado, dict):
                continue

            legacy = resultado.get("legacy") or {}
            medios = (legacy.get("extended_entities") or {}).get("media") or []
            videos = [m for m in medios if m.get("type") in ("video", "animated_gif")]
            if not videos:
                # La pestana de medios trae tambien fotos, y de aqui solo sale
                # lo que se pueda publicar como video.
                continue

            id_tweet = legacy.get("id_str")
            if not id_tweet:
                continue

            # El autor sale del propio tweet y no del perfil consultado: la
            # atribucion tiene que acreditar a quien lo publico.
            usuario = ((resultado.get("core") or {}).get("user_results") or {}).get("result") or {}
            perfil = usuario.get("core") or usuario.get("legacy") or {}
            autor = str(perfil.get("screen_name") or cuenta)

            video = videos[0]
            duracion = (video.get("video_info") or {}).get("duration_millis")

            candidatos.append(
                RawCandidate(
                    source=self.name,
                    source_id=str(id_tweet),
                    permalink=f"https://x.com/{autor}/status/{id_tweet}",
                    title=_sin_enlaces(str(legacy.get("full_text") or ""))[:500],
                    author=autor,
                    author_url=f"https://x.com/{autor}",
                    kind=(
                        MediaKind.ANIMATION
                        if video.get("type") == "animated_gif"
                        else MediaKind.VIDEO
                    ),
                    # Siempre via yt-dlp contra el permalink: el descargador ya
                    # le pasa las cookies de esta fuente.
                    media_url=None,
                    thumbnail_url=video.get("media_url_https"),
                    duration_seconds=float(duracion) / 1000 if duracion else None,
                    created_at=_fecha_de_x(legacy.get("created_at")),
                    engagement=int(legacy.get("favorite_count") or 0)
                    + int(legacy.get("retweet_count") or 0),
                    comments=int(legacy.get("reply_count") or 0),
                    nsfw=bool(legacy.get("possibly_sensitive")),
                    language=legacy.get("lang"),
                    extra={"backend": "scrape"},
                )
            )
        return candidatos


def build_x_source(*args: Any, **kwargs: Any) -> SourceAdapter:
    """Devuelve el backend de X que corresponda segun `SCRAPPY_X_BACKEND`."""
    settings = args[0] if args else kwargs["settings"]
    if settings.x_backend is XBackend.SCRAPE:
        return XScrapeSource(*args, **kwargs)
    return XApiSource(*args, **kwargs)


def _parse_iso(value: Any) -> datetime:
    """Fecha ISO-8601 de la API de X, tolerando el sufijo `Z`."""
    if not isinstance(value, str):
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return utcnow()


async def sondear_cuenta(settings: Settings, cuenta: str) -> str:
    """Cuantos videos trae una cuenta de X, para avisar al anadirla.

    De la pestana de medios solo salen tweets con video: una cuenta que publique
    solo fotos gasta su ronda y no trae nada. Paso de verdad con `@Memes`, que
    dio 0 videos de 20 medios y se estuvo consultando durante dias.

    Devuelve una frase, no un numero: la usan Telegram y la TUI tal cual, y lo
    que hace falta decir cambia segun el caso. Nunca levanta -esto es un aviso
    de cortesia, y que falle no puede impedir guardar la cuenta-.
    """
    from scrappy.config.loader import SourceConfig
    from scrappy.sources.registry import build_http_client

    limpio = cuenta.lstrip("@")
    async with build_http_client() as client:
        fuente = XScrapeSource(settings, SourceConfig(), client)
        cookies = fuente._cookies()
        if not cookies:
            return f"@{limpio}: guardada, pero sin sesion de X no se pudo comprobar."
        try:
            items = await fuente._medios(limpio, 20, cookies)
        except SesionInvalidaError:
            return f"@{limpio}: guardada. La sesion de X no vale, asi que no se comprobo."
        except SourceError as exc:
            if "no existe la cuenta" in str(exc):
                return f"@{limpio}: guardada, pero X dice que esa cuenta no existe."
            return f"@{limpio}: guardada, no se pudo comprobar ({exc})."
        videos = len(fuente._a_candidatos(items, limpio))

    if not items:
        return f"@{limpio}: guardada, pero no publica medios."
    if not videos:
        return (
            f"@{limpio}: guardada, pero de sus ultimos {len(items)} medios "
            "**ninguno es video**, asi que gastara su ronda sin traer nada."
        )
    return f"@{limpio}: {videos} videos de sus ultimos {len(items)} medios."


def _sin_enlaces(texto: str) -> str:
    """Quita el `t.co` final, que en un tweet con video apunta al propio video."""
    return _ENLACE_TCO.sub("", texto).strip()


def _fecha_de_x(valor: Any) -> datetime:
    """La fecha de la GraphQL, en el formato de siempre de Twitter.

    Ejemplo real: `Sun Aug 23 03:00:04 +0000 2026`. Si no se puede leer se
    asume «ahora», que es lo conservador: el filtro de antiguedad no descarta
    el item por un fallo de formato.
    """
    if not isinstance(valor, str):
        return utcnow()
    try:
        return datetime.strptime(valor, "%a %b %d %H:%M:%S %z %Y").astimezone(UTC)
    except ValueError:
        return utcnow()


def _cabecera_de_cookies(cookies: dict[str, str]) -> str:
    """Las cookies como cabecera `Cookie`, sin tocar el tarro del cliente."""
    return "; ".join(f"{nombre}={valor}" for nombre, valor in cookies.items())
