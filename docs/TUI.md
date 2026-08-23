# La interfaz de terminal

Scrappy se puede manejar entero desde una TUI, sin recordar flags ni abrir la documentación. Se abre con **doble clic en `Scrappy.bat`**, o desde la terminal:

```bash
scrappy tui
```

---

## Las tres pantallas

| Tecla | Pantalla | Para qué |
|---|---|---|
| `d` | **Panel** | Estado del sistema, fuentes y control del scheduler |
| `c` | **Candidatos** | Ver qué publicaría y por qué; previsualizar y publicar |
| `s` | **Configuración** | Editar el `.env` y `sources.yaml` enteros |

Y cuatro teclas globales: `?` abre la ayuda con todos los atajos, `r` refresca la pantalla actual, `R` recarga la configuración, `q` sale.

### Panel

Lo mismo que `scrappy health` y `scrappy sources`, pero de un vistazo: ffmpeg, backend de estado, Telegram, workspaces activos, la tabla de las nueve fuentes con lo que le falta a cada una, y cuántos items se han publicado.

**Scheduler.** Es la primera sección después de Sistema, porque «¿cuándo publica?» es lo que más se consulta. Si la configuración lo tiene activado, **arranca solo al abrir la TUI**, igual que hace `scrappy run`:

```
en marcha: 5 items cada 180 min
proxima ronda: hoy a las 19:52
```

La hora va en tu zona horaria y en lenguaje corriente, no en un ISO en UTC. Desde Telegram la misma información está en `/start` y `/health`, que es donde hay que mirarla cuando Scrappy corre de fondo y no hay ventana.

Los botones **Arrancar**, **Pausar** y **Reanudar** equivalen a `/pause` y `/resume` del bot, y solo están habilitados cuando pueden hacer algo.

Cuando no hay rondas, el panel dice **cuál de los tres motivos** es, porque el remedio de cada uno es distinto:

| Lo que dice | Qué hacer |
|---|---|
| `desactivado en la configuracion` | Encender «Scheduler activo» en Configuración → Programación, y guardar |
| `no puede arrancar: falta configurar Telegram` | Rellenar el token y el chat destino |
| `activado en la configuracion, pero sin arrancar` | Pulsar **Arrancar** |

**Pausar** detiene solo las rondas automáticas; publicar a mano y `/fetch` siguen funcionando. La pausa **sobrevive a una recarga**: guardar un ajuste no reanuda por su cuenta algo que paraste a propósito.

**Arranque automático.** Un interruptor para que Scrappy arranque al iniciar sesión, en segundo plano y sin ventana. Es la respuesta a «cerré la ventana y dejó de publicar»: sin esto, Scrappy solo corre mientras la tengas abierta ([OPERATIONS.md](OPERATIONS.md#cuándo-corre-scrappy)).

Registra una tarea del sistema, así que pide confirmación y dice cómo quitarla. No hace falta administrador. Solo en Windows: en Linux y macOS lo correcto es la unidad de systemd de [DEPLOYMENT.md](DEPLOYMENT.md), y el panel lo dice en vez de dejar dos botones muertos.

Si tienes el arranque automático **y** el scheduler de la ventana a la vez, el panel te avisa: son dos procesos que pueden publicar. No saldrá nada repetido —la deduplicación lo impide— pero conviene saberlo.

**Configuración.** Qué `.env` se está usando, qué zona horaria se resolvió y dónde está el catálogo, con un botón para **recargar** sin reiniciar.

**Renovar cookies de X.** Junto al de recargar. Reextrae la sesión de X del navegador que diga `SCRAPPY_X_COOKIES_BROWSER` y la deja lista, sin salir de la TUI y sin cerrar el navegador. Solo hace falta con `SCRAPPY_X_BACKEND=scrape`, y el botón lo dice si lo pulsas sin que aplique. Es el mismo código que `scrappy cookies` y que el botón de `/sources` en Telegram, así que los tres contestan lo mismo.

### Candidatos

Es el `--dry-run` de la CLI convertido en algo navegable. **Explorar** (`e`) ejecuta el pipeline en seco —sin descargar ni publicar nada— y llena la tabla con lo que habría elegido.

Al moverte por las filas, el panel derecho explica de dónde sale la nota: fuente, autor, engagement, comentarios, antigüedad y el veredicto. Eso es lo que convierte la tabla en una herramienta de calibración: sin ver el porqué, ajustar los pesos es adivinar ([RANKING.md](RANKING.md)).

**Vista previa** (`v`) muestra el caption exacto que recibiría Telegram, con su contador de caracteres. Antes, la única forma de saber cómo quedaría un post —si activar `show_score`, si la insignia de fuente estorba— era publicarlo.

**Publicar** (`p`) sí envía a Telegram, pero antes abre una confirmación que dice cuántos items van, a qué chat, y cuáles encabezan la lista. El foco arranca en *Cancelar*, así que pulsar Enter sin leer no publica nada.

### Configuración

Cubre las **dos capas** de configuración del proyecto, repartidas en pestañas:

| Pestañas | Qué contienen |
|---|---|
| Telegram · Contenido · Programación · Almacenamiento | Todo el **`.env`**: credenciales, límites, NSFW, backend de estado, nivel de log |
| Ranking · Filtros · Publicación | Pesos **y penalizaciones**, palabras y autores vetados, idiomas, presentación |
| Una por fuente | Su `weight`, `budget`, sus listas y **sus claves propias** (`window_hours` de Bluesky, `sort` de Lemmy, `subreddits_per_run` de Reddit…), más un **interruptor para activarla** |

**Salen las nueve fuentes, siempre.** Si tu `sources.yaml` no tiene la sección de alguna —pasa con los ficheros creados antes de que existiera ese adapter—, su pestaña lo dice en vez de desaparecer: la fuente funciona con los valores por defecto y su interruptor sigue sirviendo, porque vive en el `.env`. Para ajustarla, copia su bloque de `config/sources.example.yaml`.

Cada campo lleva debajo una línea explicando *por qué* tocarlo. Cinco cosas más que conviene saber:

- **Los secretos salen enmascarados.** Un token visible en pantalla es un token que se filtra en una captura. El interruptor «Mostrar secretos» los revela cuando hace falta comprobarlos.
- **Los comentarios de ambos ficheros se conservan.** Explican por qué cada valor es el que es, y son lo primero que necesitas al volver meses después.
- **Se valida antes de escribir.** Si pones un peso fuera de rango, te lo dice y **no toca el fichero**: te quedas con lo que tenías en vez de con una configuración rota que impida arrancar.
- **Solo se escribe lo que tocas.** El resto del fichero se queda exactamente como estaba.
- **Guardar ya aplica.** El `.env` se recarga en caliente al guardar, sin reiniciar el proceso ni cerrar la ventana. Los cambios de `sources.yaml` valen en la siguiente ronda, como siempre.

> **Lo que ves es lo que hace el bot, no lo que pone el fichero.** Una clave ausente del `.env` no está desactivada: usa su valor por defecto, y aquí aparece con ese valor. Es la distinción que conviene tener clara al leer un interruptor.
>
> Una versión anterior mostraba esas claves como apagadas y, al guardar, escribía ese `false` — así se apagaron dos fuentes que llevaban semanas publicando. Ya no: el interruptor refleja el valor efectivo, y guardar solo escribe lo que se ha tocado.

Lo que el editor de YAML **no** hace es crear ni borrar secciones: solo cambia valores de claves existentes. Añadir una fuente nueva sigue siendo trabajo manual, con su comentario explicando el porqué.

---

## Sin credenciales de Telegram

La TUI arranca igualmente, en **modo solo lectura**: puedes explorar candidatos y calibrar el ranking, pero el botón de publicar queda deshabilitado y la barra de estado dice por qué. Es deliberado: para ajustar los pesos no hace falta un bot.

---

## El lanzador

`Scrappy.bat` está escrito para que un doble clic nunca acabe en una ventana que se cierra sin explicar nada:

- Se sitúa en su propia carpeta, así funciona también desde un acceso directo.
- Si falta el entorno virtual, dice cómo crearlo y espera a que pulses una tecla.
- Prefiere **Windows Terminal** si está instalado: da color real y dibuja bien los bordes y los emoji, cosa que la consola clásica hace regular. Si no está, funciona igualmente.
- Ante un error, señala el log y el comando de diagnóstico, en vez de desaparecer.

---

## Dónde van los logs

Mientras la TUI corre, **Textual es dueño del terminal**. Un solo evento de structlog escrito en pantalla pintaría basura encima de la interfaz, así que los logs van a fichero:

```
data/scrappy-tui.log
```

Es el primer sitio donde mirar si algo falla. Los tokens y cookies se redactan igual que siempre, así que el fichero se puede compartir.

---

## Problemas comunes

**Se ven cuadros o interrogaciones en vez de bordes.** Estás en la consola clásica de Windows. Instala [Windows Terminal](https://aka.ms/terminal); el `.bat` lo detecta y lo usa solo.

**«Scrappy no ha arrancado».** El arranque falló. La barra de estado dice por qué, y el detalle completo está en `data/scrappy-tui.log`. Lo más habitual es un `.env` mal formado.

**El botón de publicar está gris.** Falta `SCRAPPY_TELEGRAM_BOT_TOKEN` o `SCRAPPY_TELEGRAM_TARGET_CHAT_ID` en tu `.env`. Comprueba con `scrappy whoami` que el token es válido y que el bot accede al chat.

**Explorar no devuelve nada.** No es la TUI: es que ninguna fuente encontró candidatos. `scrappy sources` dice qué le falta a cada una, y [TROUBLESHOOTING.md](TROUBLESHOOTING.md) cubre los casos por fuente.

---

## Para desarrollar

La TUI es una interfaz más, al mismo nivel que `cli.py` y `bot/`. No duplica lógica: consume `ScrappyApp`, el mismo composition root ([ARCHITECTURE.md](ARCHITECTURE.md)).

```
src/scrappy/tui/
├── main.py            # App, navegación, arranque y apagado
├── screens/           # panel, candidatos, configuración
├── widgets/confirm.py # modal de confirmación
├── yaml_editor.py     # round-trip de sources.yaml
└── scrappy.tcss       # estilos
```

Los tests usan el `Pilot` de Textual y corren sin terminal real ni red:

```bash
pytest tests/unit/test_tui.py -v
```

El más importante es `test_cancelar_el_modal_no_publica`: publicar es irreversible, y esa confirmación es lo único que separa un pulsado accidental de un mensaje en el canal.

Por qué Textual y por qué ruamel: [ADR-0010](adr/0010-tui-con-textual.md).
