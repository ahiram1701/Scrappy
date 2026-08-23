# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Versionado según [SemVer](https://semver.org/lang/es/).

## [No publicado]

### Añadido — la sesión de X se renueva sola

X es la única credencial del proyecto que caduca sin avisar, y hasta ahora arreglarla pedía acordarse de demasiadas cosas: de que existe un perfil de Firefox llamado `burner`, de que su carpeta se llama `pTbhVY6z.Profile 1` y no `burner`, de la invocación exacta de yt-dlp, y de filtrar el fichero para que no viajara la sesión del correo. Dentro de tres meses eso no se acuerda nadie.

- **`scrappy cookies`** reextrae la sesión y la deja lista. No hace falta cerrar el navegador: yt-dlp copia la base de datos antes de leerla.
- **El mismo botón en la TUI y en `/sources`.** Desde el móvil le dices «renueva» y Scrappy relee el perfil del navegador del equipo. Las tres caras llaman al mismo código, así que dicen exactamente lo mismo.
- **Se renueva sola.** Cuando X deja de tratarle como sesión iniciada, Scrappy reextrae, reintenta la ronda y sigue. Una vez por ronda, no en bucle.
- **Te avisa solo si hace falta que hagas algo**: una vez, a los administradores, cuando la renovación automática tampoco bastó. Se rearma cuando vuelve a funcionar.
- **Basta con el nombre del perfil.** `SCRAPPY_X_COOKIES_BROWSER=firefox:burner` funciona; la carpeta la resuelve leyendo `profiles.ini`.
- **`scrappy doctor` lo comprueba** —y con él `/start` y la TUI—, en dos pasos: que el fichero tenga `auth_token` y `ct0`, y que la sesión responda de verdad. Lo segundo es lo que importa: las cookies caducan dentro de un año, así que la fecha no avisa de nada.
- **Al añadir cuentas de X se comprueban.** Te dice cuántos vídeos traen de sus últimos medios. Avisa, no bloquea. Existe por `@Memes`, que estuvo consultándose días dando 0 vídeos de 20 medios porque solo publica fotos.

### Arreglado — la sesión muerta se notaba antes del 401, y la renovación no llegaba a saltar

X solo referencia su bundle JS en la portada de una sesión iniciada; sin sesión sirve una página de aterrizaje. Como los `queryId` salen de ese bundle, la fuente moría con «no se encontró el bundle» —un error genérico— y la renovación automática no se disparaba nunca. Ahora eso es lo que es: la primera señal de que la sesión murió.

Y una sesión muerta **se propaga** en vez de quedarse en un log por cuenta. No es un problema de una cuenta sino de la fuente entera, y tragárselo significaba que el aviso a Telegram no salía jamás.

### Arreglado — el backend `scrape` de X no funcionaba, y no podía

Estaba escrito sobre una premisa falsa: que yt-dlp sabe enumerar perfiles de X. No lo sabe. Sus extractores de Twitter cubren tweets sueltos, cards, spaces y broadcasts — ninguno hace timelines. Como `XScrapeSource` construía URLs de perfil, la fuente devolvía `Unsupported URL` en cada ronda, con cookies o sin ellas. No es que se rompiera con el tiempo: **no funcionó nunca**, y nadie lo vio porque venía desactivada y fallaba en un `warning`.

Como el pipeline separa descubrir de descargar, solo hacía falta cambiar la primera mitad. Ahora los tweets se piden a la GraphQL interna de la web de X con las cookies de la cuenta, y la descarga la sigue haciendo yt-dlp contra `/status/<id>`, que sí resuelve.

- **Los `queryId` se descubren solos** del bundle JS de la propia web. X los rota sin avisar, y fijarlos en el código habría sido firmar que esto caduque en una fecha desconocida. Se pueden poner a mano en `sources.yaml` si el bundle cambia de forma.
- **El id de cada cuenta se cachea**: a partir de la segunda ronda cuesta una petición en vez de dos.
- **De las cookies solo se leen las de X.** El fichero que produce `--cookies-from-browser` trae el perfil entero del navegador, incluida la sesión del correo con el que se registró la cuenta. Eso no tiene por qué viajar a X.
- **Las cookies van en la cabecera, no en el cliente HTTP**, que lo comparten todas las fuentes.
- La sesión caducada se explica en vez de dar un `HTTP 403` a secas, y `/sources` avisa si al fichero le falta `auth_token` o `ct0`.

Esto es más intrusivo que lo que había, no menos, y conviene no maquillarlo: se pasa de leer páginas públicas a llamar autenticado a una API privada. Además esa API responde 403 al User-Agent honesto de Scrappy, así que esta fuente —la única del proyecto— manda uno de navegador. Está aislada en su módulo y documentado en [LEGAL.md](docs/LEGAL.md) y [ADR-0003](docs/adr/0003-x-doble-backend.md).

### Añadido — X por scraping, y el freno que le faltaba

Activar X con el backend `scrape` es una decisión del usuario y sigue siéndolo. Lo que no era una decisión de nadie es que, al hacerlo, Scrappy pidiera los perfiles **uno detrás de otro y sin pausa**. Era la única familia de fuentes sin espaciado: Reddit, Lemmy, Bluesky, Imgur y Giphy lo tenían todas.

Peor: cuando la plataforma respondía con un límite, seguía adelante con el resto de la lista. Reddit hace lo contrario desde el primer día, y por una razón escrita en su propio código —insistir cuando ya te están limitando solo empeora las cosas—.

- **Las colecciones se rotan y se espacian.** `objetivos_por_ronda` y `delay_seconds` en `sources.yaml`, con la misma mecánica de rotación por horas que ya usaba Reddit, ahora compartida. X viene con **un perfil por ronda y 30 s entre perfiles**; la lista entera se sigue cubriendo, solo que repartida entre rondas.
- **Un rate limit corta la ronda** en vez de confirmar el patrón que te delató. Lo ya recogido se conserva: parar no es fracasar.
- **yt-dlp también respira** dentro de cada perfil, y deja de reintentar tres veces contra quien acaba de rechazarnos.
- **X exige cookies para dar la fuente por lista.** Antes `/sources` decía «lista» de algo que iba a fallar en silencio tres horas después, en un log que nadie mira. Que sean de una **cuenta desechable** está ahora dicho en todos los sitios donde se configuran.

### Arreglado — el aviso de las fuentes de riesgo desaparecía al aceptar el flag

El candado 🔒 del menú de Telegram dependía de que `ENABLE_TOS_RISKY_SOURCES` estuviera apagado. En cuanto se activaba —y ese flag se acepta una vez, para siempre— X, TikTok, Instagram y YouTube pasaban a verse **idénticas a Reddit**, y encender cualquiera de ellas era un toque sin confirmación. Quien aceptó el flag hace tres meses ya no se acuerda.

- Marca **⚠️** para las fuentes de riesgo encendidas, que sobrevive al flag.
- **Encenderlas pide confirmación.** Apagarlas no pregunta nunca: retirarse siempre es seguro.
- La TUI enseña por fin **el backend de X y su fichero de cookies**, que era el ajuste que decide si la fuente usa la vía oficial o la que incumple los términos y no se podía ni ver desde la interfaz. Su aviso dice ahora cuál de los dos está puesto, en vez de nombrar los dos.
- En Telegram, la ficha de X rotula qué campo lee cada backend: con `scrape`, las búsquedas no las mira nadie.

### Arreglado — se colgaba al arrancar con el equipo si la red no estaba lista

Tras un reinicio, Scrappy se quedó parado para siempre en `state_backend_ready`: construir la aplicación inicializa el bot contra Telegram, y esa llamada, hecha un minuto después de encender con el wifi aún sin asociar, no volvía nunca. Los reintentos de conexión que ya había protegían la **escucha**, una etapa más tarde; colgarse ocurría al **construir**, que no tenía red de seguridad.

Peor todavía: un proceso colgado no termina, así que la tarea del Programador tampoco lo reintentaba. Ahora el montaje tiene un límite de 45 s y seis intentos, y si no lo consigue **termina con error**, que es lo que deja a Windows volver a intentarlo.

### Añadido — administrar Scrappy desde el móvil

Con el arranque automático puesto, Scrappy corre sin ventana y el móvil es el único sitio desde donde se le habla. Le faltaba lo esencial: ver cómo está y poder tocar las fuentes.

- **`/status`**: en marcha desde cuándo, cómo arrancó, próxima ronda, **qué hizo la última** (o qué falló), publicado, fuentes listas y qué falta. Lo que antes obligaba a encadenar cuatro comandos. Los cinco de siempre se quedan para el detalle.
- **Configurar las fuentes con botones**: encender y apagar cada una, y editar de dónde saca el contenido —subreddits, comunidades, búsquedas, hashtags, cuentas—. Para añadir, el bot te pregunta y tú respondes a su mensaje.
- Se acepta lo que uno escribe de verdad (`r/memes`, `#gatos`, una URL de Reddit pegada) y se guarda limpio; lo que no sirve se rechaza diciendo por qué.
- **Los orígenes se aplican al momento**; el interruptor vive en el `.env` y **recarga Scrappy solo**, avisándote cuando vuelve. Si no hay quien recargue, lo dice en vez de prometerlo.
- **Las fuentes tras el aviso legal no se activan desde el móvil.** El botón responde explicando qué hace falta, y no escribe nada: un consentimiento que se da sin leer no es un consentimiento.
- Tus comentarios de `sources.yaml` sobreviven a cualquier edición.

### Arreglado — cambiar los subreddits no se aplicaba hasta reiniciar

El pipeline releía `sources.yaml` en cada ronda, pero cada adaptador se quedaba con la copia que recibió al arrancar y solo se le refrescaba el `budget`. Cambiar de dónde saca el contenido —lo único que se toca a menudo— no surtía efecto hasta reconstruir la aplicación, **mientras la TUI decía «Se aplica en la proxima ronda»**. Ahora el catálogo refresca también a los adaptadores y la frase es cierta.

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
