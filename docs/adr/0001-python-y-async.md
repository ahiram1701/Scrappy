# ADR-0001: Python asíncrono como base

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

El bot hace tres cosas a la vez: consultar varias plataformas por HTTP, descargar y transcodificar medios, y atender comandos de Telegram sin quedarse mudo mientras tanto. Ese último punto es el que decide: si el proceso se bloquea 40 segundos transcodificando un video, `/health` no responde y el bot parece caído.

## Decisión

**Python 3.11+ con `asyncio`.** Todo el pipeline es asíncrono, y el trabajo bloqueante (yt-dlp, ffmpeg, hashing) se manda a hilos con `asyncio.to_thread`.

Razones concretas:

- **yt-dlp es Python.** Es el estándar de facto para extraer medios de decenas de plataformas y ninguna alternativa se le acerca. Usarlo desde otro lenguaje significaría lanzarlo como subproceso y perder el acceso a sus metadatos.
- **python-telegram-bot** es maduro, asíncrono y trae `AIORateLimiter`, que resuelve solo el rate limiting de Telegram.
- **Pillow + ImageHash** para el hash perceptual, sin dependencias nativas complicadas.
- El descubrimiento son peticiones HTTP independientes: `asyncio.gather` las paraleliza en una línea.

Se exige **3.11** como mínimo por `StrEnum`, `datetime.UTC` y las mejoras de rendimiento de asyncio.

## Alternativas descartadas

**Go.** Un binario único, sin entorno virtual, y muy buena concurrencia. Se descarta porque yt-dlp habría que invocarlo como subproceso y parsear su JSON, perdiendo el acceso directo a sus estructuras. Toda la ventaja se la come esa fricción.

**Node/TypeScript.** Ecosistema decente de Telegram, pero el equivalente a yt-dlp son *wrappers* que lo invocan igualmente, y el ecosistema de procesamiento de imágenes es más pobre.

**Python síncrono con hilos.** Más simple de leer, pero coordinar el bot y el scheduler con hilos y locks es notablemente más frágil que con corrutinas, y `python-telegram-bot` v21 ya es asíncrono de todas formas.

## Consecuencias

- Hace falta un entorno virtual o Docker. La imagen lo resuelve.
- Todo el código de I/O es `async`. Bloquear el bucle de eventos por descuido es el error más fácil de cometer, y por eso todas las llamadas a yt-dlp y ffmpeg pasan explícitamente por `asyncio.to_thread`.
- Se soporta hasta 3.14 (probado), pero la imagen Docker fija **3.12**, que es la versión sobre la que se prueba en CI y para la que todas las dependencias tienen ruedas precompiladas.
