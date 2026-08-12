# ADR-0010: TUI con Textual, y ruamel para editar la configuración

**Estado:** aceptada · **Fecha:** 2026-08-12

## Contexto

Scrappy se manejaba solo por CLI. Funciona, pero obliga a recordar flags (`fetch --dry-run --source lemmy -n 2`) y a abrir una terminal cada vez. El requisito fue una interfaz completa que se abra **con doble clic** y permita ver el estado, explorar qué se publicaría y por qué, publicar, controlar el scheduler y ajustar la configuración.

## Decisión

**Textual** para la interfaz y **ruamel.yaml** para el editor de configuración. Ambas como dependencias del núcleo, no como extra opcional.

### Por qué Textual

- **Es async-nativo.** El pipeline de Scrappy es `asyncio` de arriba abajo; una librería síncrona habría obligado a un hilo puente y a sincronizar estado entre los dos mundos.
- **Ya dependíamos de `rich`**, que es su base. La superficie nueva es menor de lo que parece.
- **Declara soporte de Python 3.14**, la versión del entorno. Comprobado antes de escribir una sola línea de interfaz, porque era el riesgo que podía tumbar el plan.
- **Se prueba sin terminal.** `App.run_test()` da un `Pilot` que pulsa teclas y hace clic, así que la TUI tiene tests de verdad y no solo "compila".

### Por qué en el núcleo y no en un extra `[tui]`

El plan inicial decía extra. Se cambió al empezar: la TUI se abre con doble clic, y en ese contexto un `ModuleNotFoundError: textual` es exactamente la ventana que se cierra sin explicar nada contra la que está diseñado el lanzador. El coste es ~5 MB en la imagen Docker; la fiabilidad los vale.

### Por qué ruamel y no PyYAML

`config/sources.yaml` está lleno de comentarios que **no son adorno**: explican por qué Reddit rota subreddits (el rate limit), por qué Bluesky necesita `window_hours` (sin él, `sort=top` devuelve lo más votado de siempre) o que Giphy no expone contadores. Todo eso se descubrió probando contra las APIs reales, y es lo primero que alguien necesita al volver al fichero.

`yaml.safe_dump` los borra enteros. Un editor que destruye la documentación del fichero que edita no es un editor, es una trampa. `ruamel.yaml` en modo round-trip conserva comentarios, orden, comillas y formato.

Hay un test que lo fija contra el fichero real del proyecto, no contra uno de laboratorio.

### El editor solo cambia valores existentes

No crea ni borra secciones. Es una limitación deliberada: generar estructura desde una interfaz es donde más daño haría, y el fichero de ejemplo ya trae todas las secciones con sus comentarios. Añadir una fuente sigue siendo trabajo manual, con su explicación.

## Dos cambios que hicieron falta en el núcleo

Ambos **aditivos**: sin usarlos, la CLI y el bot se comportan igual que antes, y los 220 tests que ya existían siguieron pasando sin tocarlos.

**`configure_logging(log_file=...)`.** Textual es dueño del terminal mientras corre, así que un evento de structlog escrito en stdout pinta basura sobre la interfaz. Con `log_file` van a `data/scrappy-tui.log`. Detalle que se escapa fácil: hay que desactivar el color explícitamente, porque `sys.stdout.isatty()` sigue siendo `True` aunque el handler apunte a disco, y el fichero acabaría lleno de escapes ANSI.

**`Pipeline.run(on_progress=...)`.** Un run tarda unos 15 segundos. Sin avisos intermedios la interfaz se queda congelada sin poder decir en qué va. El callback se invoca desde el mismo bucle de eventos, así que puede tocar la interfaz sin preocuparse por hilos. Va envuelto para que un fallo en la interfaz que escucha no tumbe un run que por lo demás iba bien: manda el pipeline, no la interfaz.

## Alternativas descartadas

**Una GUI de escritorio** (Tkinter, PySide). Cumpliría lo del doble clic, pero duplicaría el trabajo de presentación que la CLI ya resuelve con `rich`, y añadiría un empaquetado que este proyecto no necesita. La terminal es además el entorno natural de una herramienta que ya se opera por consola y por Docker.

**Una interfaz web local** (FastAPI + navegador). Más flexible, pero implica servir un puerto, gestionar el ciclo de vida del servidor y decidir qué pasa si alguien más lo alcanza. Para una herramienta personal es mucha superficie a cambio de poco.

**`prompt_toolkit` a pelo.** Es la base de muchas TUIs y da control total, pero habría que construir tablas, layout y navegación desde cero.

**Un `.exe` con PyInstaller.** Resolvería el doble clic sin depender del venv, pero congela las dependencias, complica actualizar yt-dlp —que es justo la que hay que actualizar más a menudo— y añade un paso de build a un proyecto que hoy se instala con `pip install -e .`.

## Consecuencias

- La TUI se prueba de verdad: 14 tests con el `Pilot`, incluido el que verifica que **cancelar la confirmación no publica nada**.
- La confirmación de publicar arranca con el foco en *Cancelar*. Publicar es irreversible y un Enter distraído no debería bastar.
- Sin credenciales de Telegram la TUI arranca en modo solo lectura en vez de fallar, porque para calibrar los pesos no hace falta un bot.
- El `.bat` prefiere Windows Terminal: la consola clásica dibuja regular los bordes y los emoji que usa Textual.
- Las capturas de la documentación se generan con `App.save_screenshot()` en modo headless, así que se pueden regenerar cuando la interfaz cambie.
