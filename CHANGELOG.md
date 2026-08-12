# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Versionado según [SemVer](https://semver.org/lang/es/).

## [No publicado]

### Añadido — interfaz de terminal

- **TUI completa con Textual**, que se abre con doble clic en `Scrappy.bat` o con `scrappy tui`. Ver [docs/TUI.md](docs/TUI.md) y [ADR-0010](docs/adr/0010-tui-con-textual.md).
  - **Panel**: salud del sistema, tabla de fuentes, estadísticas y control del scheduler.
  - **Candidatos**: el `--dry-run` navegable, con el desglose de por qué cada item sacó su nota, y publicación con confirmación previa que arranca con el foco en *Cancelar*.
  - **Configuración**: editor de `sources.yaml` que **conserva los comentarios** del fichero y valida antes de escribir.
  - Sin credenciales de Telegram arranca en modo solo lectura en vez de fallar.
- `Scrappy.bat`: detecta el venv, prefiere Windows Terminal y explica qué falta en vez de cerrarse.
- Dos ganchos aditivos en el núcleo, que no cambian el comportamiento de la CLI ni del bot:
  - `Pipeline.run(on_progress=...)` para informar del avance.
  - `configure_logging(log_file=...)`, imprescindible porque Textual es dueño del terminal.

### Corregido

- El README decía «ocho fuentes» y son **nueve**. Lo detectó un test de la TUI que cuenta contra el registro en vez de contra un número escrito a mano.

- **Reddit pasa a usar los feeds Atom públicos en vez de la API OAuth.** Reddit cerró el registro autoservicio de aplicaciones en noviembre de 2025 y bloqueó los endpoints `.json` en mayo de 2026; los feeds siguen abiertos y no piden credenciales. Ver [ADR-0009](docs/adr/0009-reddit-por-rss.md).
  - El engagement se deriva de la posición en el feed, que viene ordenado por score.
  - Los subreddits rotan entre ejecuciones para no agotar el rate limit.
  - Se validan las respuestas: una página de bloqueo con 200 ya no pasa por feed vacío.
  - Los posts fijados se degradan al engagement mínimo en vez de llevarse la mejor nota.

### Añadido

- **Lemmy** como fuente sin credenciales. API pública versionada; da engagement real, tipo MIME del medio, y banderas de post fijado y de cuenta bot.
- **Bluesky** como fuente de señal temprana, también sin credenciales. Usa `api.bsky.app` (el host documentado, `public.api.bsky.app`, devuelve 403 desde mediados de 2026) y acota por `window_hours`, porque `sort=top` sin acotar devuelve lo más votado de siempre.
- **Imgur** y **Giphy**, con clave gratuita de registro instantáneo. Giphy no expone contadores, así que el ranking usa la posición en `trending`.
- **YouTube Shorts** vía la búsqueda interna de yt-dlp, detrás del flag de ToS.

### Eliminado

- `SCRAPPY_REDDIT_CLIENT_ID` y `SCRAPPY_REDDIT_CLIENT_SECRET`: ya no se usan. Si los tenías en el `.env`, puedes borrarlos.

### Corregido

- El prefijo `/u/` del autor se quitaba con `lstrip`, que elimina cualquiera de esos caracteres: un autor llamado `umberto` quedaba como `mberto`.

## [0.1.0] — 2026-08-11

Primera versión.

### Añadido

**Pipeline**
- Seis etapas: descubrir → filtrar → rankear → descargar → deduplicar → publicar.
- Las cuatro últimas se ejecutan por item, no por lote, de modo que nunca hay más de un medio en la máquina.
- Modo `--dry-run` que recorre descubrimiento, filtros y ranking sin descargar ni publicar, con tabla de desglose de cada score.

**Almacenamiento efímero**
- `EphemeralWorkspace` con borrado garantizado en `finally`, ante excepción y ante cancelación.
- Imágenes y GIFs enteramente en memoria, sin fichero temporal.
- Handler de `SIGTERM`/`SIGINT` que purga los workspaces vivos antes de terminar.
- Barrido de huérfanos al arrancar.
- Workspace en tmpfs en Docker: el medio no llega al disco físico.
- Tres backends de estado (`sqlite`, `memory`, `none`) para elegir cuánto se recuerda.

**Fuentes**
- Reddit sobre la API oficial con OAuth *application-only*.
- X con doble backend: `api` (oficial, requiere tier de pago) y `scrape` (yt-dlp, contra ToS).
- TikTok e Instagram sobre yt-dlp.
- Las tres fuentes que incumplen ToS, desactivadas tras `ENABLE_TOS_RISKY_SOURCES`.

**Ranking**
- Normalización por percentil dentro de cada fuente y lote, para que las escalas de las plataformas no se mezclen.
- Término de velocidad que detecta lo que se está haciendo viral ahora.
- Penalizaciones configurables por duración, falta de miniatura y engagement sin conversación.
- Todos los pesos y umbrales en `config/sources.yaml`.

**Deduplicación**
- Tres puertas: `uid` antes de descargar, `sha256` exacto, y pHash perceptual.
- Para video, pHash de tres frames muestreados al 25/50/75 % de la duración, extraídos por stdout sin tocar el disco.

**Entrega**
- Método de la Bot API correcto según el tipo de medio.
- Atribución obligatoria y no desactivable: autor y enlace al original en cada publicación.
- Transcodificación automática a 720p cuando el clip supera el límite de 50 MB.
- Control de ritmo entre publicaciones.

**Interfaces**
- CLI: `fetch`, `run`, `health`, `sources`, `whoami`, `purge`, `version`.
- Comandos del bot: `/fetch`, `/sources`, `/stats`, `/pause`, `/resume`, `/config`, `/health`, `/purge`.
- Scheduler con `max_instances=1` para que dos runs no se solapen.

**Infraestructura**
- Imagen Docker multi-etapa con ffmpeg, usuario sin privilegios y tmpfs para el workspace.
- CI en Linux y Windows, Python 3.11–3.13, con un paso que falla si queda algún fichero de medio tras los tests.
- 140 tests, sin red. Cobertura por encima del 80 % en `ranking/`, `storage/` y `delivery/`.

**Documentación**
- Nueve documentos en `docs/` y ocho ADRs.

[No publicado]: https://github.com/Ahiram/Scrappy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Ahiram/Scrappy/releases/tag/v0.1.0
