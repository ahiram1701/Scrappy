# ADR-0002: httpx directo en vez de PRAW

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

PRAW (y su versión asíncrona, asyncpraw) es la librería estándar para la API de Reddit. Gestiona OAuth, rate limiting, paginación y expone objetos ricos.

Scrappy necesita exactamente **dos endpoints**: `POST /api/v1/access_token` y `GET /r/{sub}/{listing}`.

## Decisión

Usar **httpx directamente** contra `oauth.reddit.com`, con el flujo *application-only* (`client_credentials`), sin PRAW.

Los motivos, por orden de peso:

1. **Un solo cliente HTTP para todos los adapters.** X usa httpx, Reddit usa httpx. Un único pool de conexiones, un único sitio donde ajustar timeouts, reintentos y User-Agent. Con PRAW, Reddit sería la excepción con su propia gestión de red.
2. **Superficie de dependencias.** PRAW arrastra su propio stack para dos llamadas.
3. **Control del error.** Distinguir un 401 (credenciales muertas, aborta la fuente) de un 403 (subreddit privado, sáltalo) de un 429 (rate limit, espera) es central para el comportamiento del pipeline. PRAW lo envuelve todo en su propia jerarquía y habría que traducirla de vuelta.
4. **Testeabilidad.** Con httpx, `respx` intercepta las llamadas y los tests usan payloads reales recortados. Mockear PRAW es bastante más incómodo.

## Alternativas descartadas

**asyncpraw.** Encajaría con el modelo asíncrono, pero mantiene las tres objeciones anteriores y añade una dependencia que se mueve por su cuenta.

**Los endpoints `.json` públicos** (`reddit.com/r/memes/top.json`, sin OAuth). Funcionan sin credenciales, lo que sería más cómodo para empezar, pero tienen límites de tasa mucho más agresivos y Reddit desaconseja su uso automatizado. Usar la vía oficial es lo correcto cuando existe.

## Consecuencias

- El adapter de Reddit son ~150 líneas que hay que mantener nosotros: renovación de token, paginación, normalización.
- A cambio, el manejo de errores es exactamente el que el pipeline necesita, y los tests de Reddit son los más completos del proyecto.
- Si en el futuro hiciera falta escribir en Reddit (comentar, votar), habría que reconsiderarlo: ahí PRAW sí aporta mucho.
