# El bot de Telegram

Todo lo que Scrappy hace desde el móvil: cada comando, cada menú y cada botón, con lo que hace, lo que devuelve y si se puede deshacer.

> **Solo responden a los administradores.** Los ids de `SCRAPPY_TELEGRAM_ADMIN_IDS` son los únicos que pueden usar comandos y pulsar botones. Si esa variable está vacía, nadie puede: el scheduler sigue publicando, pero el bot no obedece a nadie. Los botones viajan dentro del mensaje, así que en un canal cualquiera podría pulsarlos alguien; por eso se comprueba **quién pulsa**, no quién recibió el teclado.

---

## Índice

- [La primera vez: `/start`](#la-primera-vez-start)
- [Comandos](#comandos)
- [Los menús de botones](#los-menús-de-botones)
- [Los botones bajo cada publicación](#los-botones-bajo-cada-publicación)
- [El menú nativo de Telegram](#el-menú-nativo-de-telegram)
- [Cuando algo falla](#cuando-algo-falla)

---

## La primera vez: `/start`

`/start` no es un saludo: es un diagnóstico. Responde a las tres preguntas que uno tiene la primera vez.

```
Hola. Soy Scrappy.
Todo listo. 9 comprobaciones correctas.

ℹ️ Publicare en el chat -1001234567890, no aqui.

Fuentes listas (3)
· reddit
· lemmy
· bluesky

Por revisar
· ffmpeg: no esta en el PATH
  Instalalo con: winget install Gyan.FFmpeg

Publicare 5 items cada 180 minutos, en horario de America/Mexico_City.
Proxima ronda: hoy a las 19:52.

Escribe / para ver todo lo que puedo hacer.
```

Si escribes `/start` en el propio canal de destino, la segunda línea es «✅ Este es el chat donde publicaré» en lugar de la anterior. Esa distinción es deliberada: confirma de un vistazo que el `chat_id` apunta a donde crees.

Qué mirar, en orden:

| Línea | Qué te dice |
|---|---|
| **Publicaré en** | A qué chat van los memes. Si aquí sale un chat que no esperabas, revisa `SCRAPPY_TELEGRAM_TARGET_CHAT_ID` antes de nada |
| **Fuentes listas** | Cuántas plataformas están activas *y* configuradas. Si sale «Ninguna fuente lista», nada va a publicarse |
| **Por revisar** | Los problemas detectados, con la orden exacta para arreglarlos. Es el mismo diagnóstico de `scrappy doctor` |
| **Publicaré N cada M** | La cadencia, y **en qué zona horaria**. Si tus publicaciones aparecen de madrugada, esta línea explica por qué |
| **Próxima ronda** | Cuándo toca la siguiente, en tu hora. Es el único sitio donde se puede consultar si Scrappy corre de fondo, sin ventana |

Esa última línea cambia según el caso, y cada variante quiere decir algo distinto:

| Lo que dice | Qué significa |
|---|---|
| `Proxima ronda: hoy a las 19:52` | Todo en orden |
| `⏸ En pausa. Usa /resume para reanudar.` | Alguien hizo `/pause`. No se promete una hora que no se va a cumplir |
| `⚠️ Ahora mismo no hay rondas programadas.` | El scheduler está habilitado pero no arrancó. Si corre desde la TUI, mira su Panel |
| `El scheduler esta desactivado…` | `SCRAPPY_SCHEDULE_ENABLED=false`. Solo publicará con `/fetch` |

Si el bot lo arrancó algo que no registró el scheduler —`scrappy run --no-bot` no llega a tener bot, pero un montaje propio sí podría—, se da la cadencia y **no** la hora: prometer una hora que no se puede consultar sería peor que no darla.

Si el bot no contesta a `/start`, el problema es anterior al bot: mira [Cuando algo falla](#cuando-algo-falla).

---

## Comandos

### `/fetch [fuente] [n]` — publicar ahora

Ejecuta el pipeline completo al momento, sin esperar al scheduler: busca, filtra, rankea, descarga, deduplica, publica y borra.

```
/fetch              → abre el menú de botones
/fetch reddit       → 1 item de Reddit
/fetch reddit 3     → 3 items de Reddit
/fetch * 5          → 5 items de cualquier fuente
```

Sin argumentos ofrece botones, que es lo cómodo desde el móvil: no hay que recordar la sintaxis ni el nombre exacto de cada fuente.

Mientras corre va editando su propio mensaje con el progreso. Al terminar, el resumen:

```
Publicados 3 de 47 candidatos en 22.4s

Incidencias:
· [imgur] falta SCRAPPY_IMGUR_CLIENT_ID
```

Las incidencias no son fallos del comando: una fuente puede caerse y las demás siguen. Se muestran las cinco primeras, y cuántas quedan si hay más.

**Cuánto tarda.** Entre 15 y 40 segundos. La mayor parte es esperar a las plataformas, que tienen límites de peticiones que hay que respetar.

---

### `/sources` — estado de cada fuente

Las nueve fuentes, activas o no, con el motivo cuando no lo están.

```
Fuentes
· reddit: lista (9 subreddits, 3 por ronda (feeds RSS, orden `hot`))
· lemmy: lista (3 comunidades en https://lemmy.world, sin credenciales)
· bluesky: lista (3 consultas, ultimas 24h, sin credenciales)
· imgur: activada pero SIN configurar - falta SCRAPPY_IMGUR_CLIENT_ID
· giphy: desactivada
· youtube: desactivada
```

La distinción importa: **desactivada** es una decisión tuya; **activada pero sin configurar** es algo a medio hacer, y esa fuente no va a aportar nada aunque su interruptor esté encendido.

---

### `/stats [días]` — qué se ha publicado

```
/stats        → botones: Hoy · 7 días · 30 días · Todo
/stats 30     → últimos 30 días directamente
/stats 0      → histórico completo
```

Reparto por fuente y total acumulado:

```
Ultimos 7 dia(s)
· bluesky: 4
· lemmy: 7
· reddit: 23

Historico total: 218
```

Sirve para ver si una fuente ha dejado de aportar sin que nadie se diera cuenta.

---

### `/pause` y `/resume` — parar el scheduler

`/pause` detiene **solo las rondas automáticas**. `/fetch` y el botón de publicar de la TUI siguen funcionando: es una pausa, no un apagado.

No sobrevive a un reinicio del proceso. Para parar de verdad y para siempre, `SCRAPPY_SCHEDULE_ENABLED=false`.

---

### `/config` — la configuración que está usando

La configuración **efectiva**, con los secretos ya redactados: el token sale como `***` porque esto se responde en un chat.

```
state_backend = sqlite
items_per_run = 5
schedule_enabled = True
schedule_interval_minutes = 180
timezone = America/Mexico_City
max_download_mb = 60
allow_nsfw = False
min_score = 0.3
telegram_bot_token = ***
```

Ojo a `timezone`: muestra la zona **resuelta**, no la escrita. Si dejaste `SCRAPPY_TIMEZONE` vacío, aquí ves cuál detectó.

Es la forma de comprobar que un cambio en el `.env` llegó de verdad al proceso: los ajustes se leen al arrancar, así que un `.env` editado sin reiniciar —o sin recargar desde la TUI— no se refleja aquí.

---

### `/health` — diagnóstico técnico, y cuándo es la próxima ronda

```
ffmpeg: OK
estado: sqlite (218 items recordados)
telegram: configurado
workspaces activos: 0 (limpio)
fuentes:
  - reddit: lista (9 subreddits, 3 por ronda (feeds RSS, orden `hot`))
  - lemmy: lista (3 comunidades en https://lemmy.world, sin credenciales)
  - bluesky: lista (3 consultas, ultimas 24h, sin credenciales)

Publicare 5 items cada 180 minutos, en horario de America/Mexico_City.
Proxima ronda: hoy a las 19:52.
```

**`workspaces activos: 0`** es la línea importante: confirma la promesa central del proyecto, que no queda contenido en el disco. Un número distinto de cero durante una ronda es normal —hay descargas en curso—; fuera de una ronda, no.

---

### `/purge` — borrar los temporales ya

En condiciones normales no hace falta: cada item borra el suyo al terminar, pase lo que pase. Está para después de un cierre brusco, y para poder comprobar que el disco queda limpio cuando uno quiere comprobarlo.

---

### `/help` — la chuleta

La lista de comandos, dentro de Telegram.

---

## Los menús de botones

### `/fetch` sin argumentos — dos pasos

**Primero, la fuente.** Solo salen las fuentes activas — las que Scrappy ha construido de verdad al arrancar. Una desactivada en el `.env` no aparece.

```
┌─────────────────────────────┐
│     Todas las fuentes       │
├──────────────┬──────────────┤
│    reddit    │    lemmy     │
├──────────────┼──────────────┤
│   bluesky    │              │
├──────────────┴──────────────┤
│          Cancelar           │
└─────────────────────────────┘
```

De dos en dos porque en el móvil tres botones por fila quedan ilegibles.

**Después, la cantidad.** El mensaje se edita, no se manda otro:

```
Cuantos items publico de reddit?

┌─────┬─────┬─────┬─────┐
│  1  │  3  │  5  │ 10  │
├─────┴─────┴─────┴─────┤
│       Cancelar        │
└───────────────────────┘
```

Al pulsar un número empieza a publicar. **No hay confirmación después de este paso**: el número *es* la confirmación.

### `/stats` sin argumentos — un paso

```
┌───────┬─────────┬──────────┬───────┐
│  Hoy  │ 7 dias  │ 30 dias  │ Todo  │
└───────┴─────────┴──────────┴───────┘
```

Se puede pulsar varias veces sobre el mismo mensaje para comparar rangos: cada pulsación reescribe el mensaje.

### Cancelar

Los menús de `/fetch` llevan **Cancelar**, que reemplaza el mensaje por «Cancelado.» y no hace nada más. `/stats` no lo lleva porque solo lee.

---

## Los botones bajo cada publicación

Cada meme publicado llega con tres botones. Son la parte con la que más se interactúa, y la que decide qué publicará Scrappy mañana.

```
┌─────────────┬────────────────┬──────┐
│  🗑 Borrar   │ 🚫 Vetar autor │  👎  │
└─────────────┴────────────────┴──────┘
```

| Botón | Qué hace exactamente | ¿Se puede deshacer? |
|---|---|---|
| **🗑 Borrar** | Quita el mensaje del canal. **No** lo borra del historial de deduplicación, a propósito: si lo borrase, ese mismo meme volvería a colarse en la siguiente ronda | El mensaje no se recupera. Telegram además solo permite borrar lo publicado hace **menos de 48 horas**; pasado ese plazo el botón responde diciéndolo |
| **🚫 Vetar autor** | Escribe el autor en `filters.blocked_authors` de `sources.yaml`. Se guarda **en el fichero**, no solo en memoria, así que sobrevive a un reinicio, y surte efecto **en la siguiente ronda** sin reiniciar nada. Es permanente y absoluto: ese autor no vuelve a aparecer | Sí: quitándolo de esa lista, desde la pestaña **Filtros** de la TUI o editando el YAML |
| **👎** | Penaliza a ese autor en el ranking, de forma **acumulativa y con tope**. Es la versión suave del veto: no lo elimina, hace que le cueste más entrar. Responde con cuántos votos lleva acumulados | Sí, pero hoy solo editando la base de datos (`data/scrappy.db`, tabla `feedback`) |

**Los tres registran su decisión** en el historial, de modo que el ranking aprende de lo que apruebas y lo que no. Ninguno vuelve a descargar nada.

### Lo que pueden responder

| Respuesta | Por qué |
|---|---|
| «Borrado» / «Anotado» | Todo bien |
| «`autor` vetado. No volverá a aparecer.» | Veto aplicado y escrito en el YAML |
| «`autor` ya estaba vetado.» | El botón se pulsó dos veces; no pasa nada |
| «No encuentro ese item en el historial…» | Con `SCRAPPY_STATE_BACKEND=memory`, un reinicio borra el historial. El mensaje sigue en el canal, pero Scrappy ya no sabe de qué item hablas |
| «De este item no se guardó el autor…» | Publicado con una versión anterior, que no guardaba el autor. Solo afecta a lo antiguo |
| «No se pudo borrar: … más de 48 horas» | Límite de Telegram, no de Scrappy |
| «No estás autorizado.» | Tu id no está en `SCRAPPY_TELEGRAM_ADMIN_IDS` |

Y una cosa que **no** verás: un botón con el reloj girando indefinidamente. Todas las pulsaciones se responden, incluso las que fallan.

### Cuando faltan los botones

Alguna publicación puede llegar sin ellos. `callback_data` no admite más de 64 bytes, y con identificadores muy largos no caben los tres botones: se publica sin ellos en vez de que Telegram rechace el mensaje entero. Es raro y no indica ningún problema.

---

## El menú nativo de Telegram

Al arrancar, el bot registra sus comandos en Telegram, que es lo que hace que aparezca el botón **☰** junto a la caja de texto y que escribir `/` autocomplete:

| Comando | Descripción en el menú |
|---|---|
| `/start` | Comprobar que todo funciona |
| `/fetch` | Buscar y publicar ahora |
| `/sources` | Estado de cada fuente |
| `/stats` | Que se ha publicado |
| `/pause` | Parar el scheduler |
| `/resume` | Reanudar el scheduler |
| `/health` | Diagnostico |
| `/help` | Ayuda |

`/config` y `/purge` no salen en el menú aposta: son de mantenimiento y llenarían la lista de cosas que no se usan a diario. Funcionan igual escribiéndolos.

Si el menú no aparece, cierra y vuelve a abrir el chat: Telegram cachea la lista. Que falle el registro no impide nada; el bot funciona igual sin menú.

---

## Cuando algo falla

### El bot no contesta a nada

Por orden de probabilidad:

1. **No hay ningún Scrappy corriendo.** Es la causa más común. Scrappy solo responde mientras hay un proceso suyo vivo — ver [OPERATIONS.md](OPERATIONS.md#cuándo-corre-scrappy).
2. **Tu id no está en `SCRAPPY_TELEGRAM_ADMIN_IDS`.** El bot te ignora en silencio, por diseño: contestar «no estás autorizado» a desconocidos es regalar información.
3. **Nunca pulsaste «Iniciar»** en el chat con el bot. Telegram no deja que un bot escriba primero.

### Contesta pero no publica

Prueba `/health` y `/sources`. Lo habitual es que no haya ninguna fuente lista, o que `min_score` esté tan alto que nada lo alcance.

### Publica a horas raras

Mira la última línea de `/start`. Si la zona no es la tuya, `SCRAPPY_TIMEZONE` en el `.env` — o déjalo vacío para que se detecte sola.

### Un cambio del `.env` no surte efecto

Los ajustes se leen **una sola vez, al arrancar**. Comprueba con `/config` qué está usando de verdad, y recarga desde la TUI (botón **Recargar configuración** en el Panel, o `R`).

---

## Y también

- [PRIMEROS_PASOS.md](PRIMEROS_PASOS.md) — de cero a la primera publicación
- [TUI.md](TUI.md) — la interfaz de terminal
- [CONFIGURATION.md](CONFIGURATION.md) — todas las variables
- [OPERATIONS.md](OPERATIONS.md) — cuándo corre Scrappy y cómo mantenerlo
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — cuando algo no va
