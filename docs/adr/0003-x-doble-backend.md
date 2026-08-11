# ADR-0003: Doble backend para X/Twitter

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

X fue una de las fuentes pedidas explícitamente. El problema es que no hay una opción buena:

- La **API oficial v2** tiene un endpoint de búsqueda (`/2/tweets/search/recent`) que es exactamente lo que necesitamos. Pero **el tier gratuito no lo incluye**: el tier gratuito solo permite publicar. Buscar requiere el tier Basic, que en el momento de escribir esto ronda los **200 USD al mes**.
- **Scrapear** con yt-dlp y cookies funciona y no cuesta dinero, pero incumple los términos de X, se rompe cuando la plataforma cambia, y puede acarrear el bloqueo de la cuenta cuyas cookies se usen.

Cualquiera de las dos opciones, elegida en solitario, deja fuera a mucha gente: la primera por precio, la segunda por riesgo.

## Decisión

Implementar **ambos backends** detrás del mismo nombre de fuente (`x`), seleccionables con `SCRAPPY_X_BACKEND`:

| Backend | Clase | Requiere |
|---|---|---|
| `api` *(por defecto)* | `XApiSource` | Bearer token con tier Basic o superior |
| `scrape` | `XScrapeSource` | Cookies **y** `ENABLE_TOS_RISKY_SOURCES=true` |

Una función `build_x_source()` devuelve la implementación adecuada; el registro y el resto del pipeline no saben cuál está activa.

Tres detalles deliberados:

1. **El defecto es `api`**, la vía limpia. Quien quiera la otra tiene que pedirla.
2. **`XApiSource` tiene `requires_tos_ack = False`** y `XScrapeSource` lo tiene a `True`. La diferencia legal está codificada en el tipo, no en un comentario.
3. **El 403 se explica.** Es el error que se va a encontrar casi todo el mundo que active X, y un "HTTP 403" a secas no dice nada. El mensaje nombra el tier, el precio aproximado y la alternativa.

## Alternativas descartadas

**Solo la API oficial.** Sería lo más limpio, pero dejaría la fuente inservible para cualquiera que no pague 200 USD al mes. En la práctica equivale a no tener fuente de X.

**Solo scraping.** Más barato para el usuario, pero convertiría el incumplimiento de ToS en el camino único y por defecto. Eso es una decisión que no corresponde tomar al proyecto.

**Instancias de Nitter.** En su momento era la vía elegante. Hoy la inmensa mayoría están caídas o bloqueadas, y depender de una infraestructura de terceros inestable sería peor que las dos opciones actuales.

**El endpoint de sindicación** (`cdn.syndication.twimg.com`). No documentado, sin garantías, y con la misma situación legal que el scraping pero con menos herramientas para mantenerlo.

## Consecuencias

- Dos implementaciones que mantener para una sola fuente.
- El usuario tiene que entender la diferencia. Está explicada en `.env.example`, en [LEGAL.md](../LEGAL.md) y en el propio `status()` de la fuente.
- El backend `scrape` se romperá periódicamente. Es inevitable y está documentado como tal en [OPERATIONS.md](../OPERATIONS.md).
- Los precios y tiers de X cambian. Si el de pago deja de ser necesario, el backend `scrape` puede retirarse sin tocar nada más del pipeline.
