# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Versionado según [SemVer](https://semver.org/lang/es/).

## [No publicado]

### Añadido — aviso por Telegram al arrancar

Con el arranque automático puesto, Scrappy se levanta sin ventana y sin nadie mirando: saber si había arrancado obligaba a abrir el log o a preguntarle con `/start`, que es justo lo que uno no quiere hacer después de reiniciar. Ahora escribe él.

- El aviso dice **cuándo arrancó** y **cuándo publicará** — las dos preguntas que uno se hace al verlo— y avisa si arrancó sin escuchar comandos.
- **Va a `SCRAPPY_TELEGRAM_ADMIN_IDS`, nunca al canal.** El canal es para el contenido; un «he arrancado» por cada reinicio sería ruido en un sitio público.
- No puede tumbar el arranque: si un administrador nunca le ha escrito al bot —Telegram no deja empezar la conversación desde el otro lado— queda en el log y los demás avisos salen igual.
- Se apaga con `SCRAPPY_NOTIFY_ON_START=false` o desde la pestaña de configuración de la TUI.

### Añadido — arrancar sin iniciar sesión

Arrancar al iniciar sesión dejaba fuera el caso que más se quiere: equipo encendido, sesión cerrada, y Scrappy sin publicar. Ahora hay dos modos.

- **Modo `sistema`**: tarea con disparador de arranque, que corre aunque no entre nadie al equipo. Botón «Sin iniciar sesión» en el panel, o `scrappy autostart --sistema`.
- Corre **como tu cuenta** (`S4U`: sin sesión y sin guardar la contraseña), con `SYSTEM` de respaldo para las cuentas que no admiten S4U. Las dos identidades se intentan en la **misma** llamada elevada: dos diálogos de UAC seguidos para una sola acción se parecen demasiado a algo que no deberías aceptar.
- Registrarla pide elevación y no hay forma de rodearlo, así que se pide de frente y, si se rechaza, se dice qué se puede hacer sin permisos en vez de fallar en seco. Quitarla vuelve a pedirla.
- **Nuevo comando `scrappy autostart`** (`--sistema`, `--sesion`, `--quitar`, o sin nada para consultar).

### Arreglado — el arranque automático no arrancaba

Estaba roto de tres formas distintas, y las tres eran invisibles: al no haber ventana, no había dónde ver el error.

- **Moría al segundo de iniciar sesión.** La tarea lanza `pythonw.exe`, que no tiene consola, y ahí `sys.stdout` es `None`: `configure_logging` le preguntaba `isatty()` y reventaba antes de arrancar nada. Ahora, sin consola, los logs se van solos a `data/scrappy.log`, que además **rota a los 5 MB** porque ese proceso no termina nunca.
- **La tarea no se podía ni crear.** Registrarla escribe en la carpeta raíz del Programador de tareas, y eso Windows solo se lo permite a un proceso **elevado** —ser administrador no basta, porque una sesión normal lleva el grupo desactivado hasta que algo pide elevación—. La TUI no la pide, así que «Activar» respondía siempre «Acceso denegado». Se añade un respaldo: un acceso directo en la **carpeta de Inicio del usuario**, que arranca lo mismo sin pedir permiso a nadie. Se sigue intentando la tarea primero, que es mejor cuando se puede. «Desactivar» quita los dos.
- **Se rendía si la red no estaba lista.** `scrappy run` daba por perdida la conexión con Telegram al primer intento, justo el momento en que un equipo recién encendido aún no tiene wifi: un día entero de bot mudo. Ahora insiste seis veces cada 30 segundos.

Además, los errores del CLI se escriben también en el log cuando no hay consola, la tarea reintenta si el proceso se cae, y las rutas del XML van escapadas. Ver [ADR-0012](docs/adr/0012-zona-horaria-y-autoarranque.md), enmendada con lo que se comprobó.

### Añadido — revisión de la experiencia de uso

Configurar el bot por primera vez costó varias rondas de depuración por dos erratas que ninguna herramienta detectaba. Esto las convierte en mensajes que dicen qué hacer, y amplía lo que se puede manejar sin editar ficheros a mano.

**Diagnóstico**
- `scrappy doctor`: revisa la configuración y explica cómo arreglar cada fallo. Detecta el token con el prefijo de la plantilla pegado delante, el chat id con el signo cambiado, el User-Agent genérico de Reddit (causa de los 429), ffmpeg ausente y las fuentes sin credenciales.
- `scrappy init`: crea el `.env` paso a paso validando cada valor **contra Telegram** antes de escribirlo.
- Las comprobaciones viven en un módulo único que consumen `doctor`, el `/start` del bot y la TUI: escritas tres veces se desincronizarían.

**La TUI, ya configurable por completo**
- Pestañas para **todo el `.env`** (credenciales, contenido, programación, almacenamiento), que antes no se tocaba.
- Las secciones del YAML que faltaban: penalizaciones, filtros y publicación.
- Los ajustes propios de cada fuente, cada uno en su pestaña, con un **interruptor para activarla**.
- Los secretos se muestran enmascarados, con interruptor para revelarlos.
- **Vista previa del caption**: cómo quedará en Telegram sin publicarlo.
- Pantalla de ayuda (`?`) con todos los atajos.

**Telegram**
- `/start` confirma que el mensaje llega, dice a qué chat publicará, qué fuentes están listas, qué le falta a las demás y cuándo publicará. Ya no es una lista de comandos.
- `/fetch` y `/stats` sin argumentos ofrecen botones.
- Menú nativo con `setMyCommands`.
- **Acciones bajo cada publicación**: borrar del canal, vetar al autor (se escribe en `sources.yaml`) y 👎, que penaliza a ese autor en el ranking de forma proporcional y con tope.

### Cambiado

- Migración de esquema **v2**: columna `author` en `published` y tabla `feedback`. Probada sobre una base v1 con datos para verificar que no se pierde el historial de deduplicación.

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
