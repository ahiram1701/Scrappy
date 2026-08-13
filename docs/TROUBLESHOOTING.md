# Cuando algo no va

Antes que nada, dos comandos que responden la mayoría de las preguntas:

```bash
scrappy health
scrappy sources
```

Y si necesitas ver qué está pasando por dentro:

```bash
SCRAPPY_LOG_LEVEL=DEBUG scrappy fetch --dry-run
```

---

## Lo que más pasa

Tres casos que no son fallos de nada, pero lo parecen.

### «Cerré la ventana y dejó de publicar»

No está roto: es como funciona. **Scrappy publica mientras hay un proceso suyo vivo**, y cerrar la ventana de `Scrappy.bat` mata ese proceso. No hay servicio de fondo esperando a menos que lo pongas tú.

Para que arranque solo al iniciar sesión: Panel → **Arranque automático** → Activar. Las cuatro formas de tenerlo corriendo, en [OPERATIONS.md](OPERATIONS.md#cuándo-corre-scrappy).

Cómo saber si hay alguno vivo: mándale `/start`. Si contesta, lo hay.

### «Publicó de madrugada»

Zona horaria. Hasta hace poco el scheduler iba en UTC, y en América eso son entre cinco y ocho horas de desfase.

Mira la última línea de `/start`, que dice en qué zona está trabajando, o `/config`. Si no es la tuya, pon `SCRAPPY_TIMEZONE=America/Mexico_City` en el `.env` —formato IANA— o **déjalo vacío**, que es lo normal: así detecta la del sistema.

Y luego recarga: el `.env` no se aplica solo (ver más abajo).

Lo que se guarda en la base de datos sigue en UTC a propósito. Solo cambia la hora de disparo y las que se muestran.

### «Una fuente aparece apagada pero estaba funcionando»

**Una clave que no está en el `.env` no queda desactivada: usa su valor por defecto.** Si tu `.env` se creó antes de que existiera una fuente, esa fuente está activa aunque no aparezca por ninguna parte en el fichero — el defecto de Reddit, Lemmy y Bluesky es `true`.

Lo que manda siempre:

```bash
scrappy sources
```

Si ves lo contrario —el `.env` dice `false` pero la fuente publica, o al revés— comprueba también las **variables de entorno del sistema**, que tienen prioridad sobre el fichero.

> Una versión anterior de la pantalla de Configuración pintaba esas claves ausentes como interruptores apagados y, al guardar, escribía ese `false`. Si te desaparecieron fuentes sin tocar nada, fue eso: vuelve a activarlas en el `.env` y ya no se repetirá.

### «Cambié el `.env` y no pasa nada»

Los ajustes se leen **una vez, al arrancar**. Guardar el fichero no basta.

Comprueba con `/config` qué está usando de verdad. Para aplicarlo sin reiniciar: Panel → **Recargar configuración**, o `R`. Guardar desde la pantalla de Configuración ya recarga por su cuenta.

`sources.yaml` es la excepción: se relee en cada ronda.

---

## Arranque

### `ffmpeg no esta en el PATH`

Es obligatorio: yt-dlp lo necesita para unir video y audio, y Scrappy para normalizar los clips.

| Sistema | Comando |
|---|---|
| Windows | `winget install Gyan.FFmpeg` — cierra y reabre la terminal |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Docker | Ya viene dentro |

Comprueba con `ffmpeg -version`. Si el comando existe pero Scrappy no lo ve, el `PATH` del proceso no incluye su directorio: en systemd, añade `Environment=PATH=...`.

### `Configuracion de Telegram incompleta`

Falta `SCRAPPY_TELEGRAM_BOT_TOKEN` o `SCRAPPY_TELEGRAM_TARGET_CHAT_ID`. ¿Copiaste `.env.example` a `.env`? El fichero tiene que llamarse exactamente `.env` y estar en el directorio desde el que ejecutas.

### `no hay ninguna fuente utilizable`

Ejecuta `scrappy sources`: cada fila dice qué le falta a esa fuente. Lo habitual:

- Reddit sin `CLIENT_ID`/`CLIENT_SECRET`.
- Reddit sin subreddits en `config/sources.yaml` (¿copiaste el ejemplo?).
- Una fuente de riesgo activada pero sin `SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true`.

---

## Telegram

### El bot no publica nada y no da error

```bash
scrappy whoami
```

Casi siempre es una de estas tres:

1. El bot **no está en el canal**, o está pero no como administrador.
2. El `chat_id` es incorrecto. Los canales y supergrupos tienen id **negativo** empezando por `-100`.
3. El bot no tiene permiso de "Enviar mensajes" en ese canal.

### `el bot no puede escribir en el chat destino`

Añádelo al canal como administrador con permiso para publicar. En un grupo normal basta con que sea miembro; en un canal necesita ser admin.

### `Telegram rechazo el medio`

Normalmente el fichero supera los 50 MB o el formato no le gusta. Scrappy ya intenta reencodar a 720p antes de rendirse. Baja `SCRAPPY_MAX_DURATION_SECONDS` para que ni lleguen candidatos tan largos.

### `Telegram pide esperar N segundos`

Estás publicando demasiado rápido. Sube `delivery.delay_between_posts` a 6–8 segundos, o baja `SCRAPPY_ITEMS_PER_RUN`. El límite ronda los 20 mensajes por minuto y por chat.

### Los comandos no responden

Tu id no está en `SCRAPPY_TELEGRAM_ADMIN_IDS`. Consíguelo con [@userinfobot](https://t.me/userinfobot). Recuerda: **si la lista está vacía, nadie puede usar comandos**, y es a propósito.

---

## Fuentes

### Reddit: `error 500` al crear una app en `/prefs/apps`

No es culpa tuya: **Reddit cerró el registro autoservicio de aplicaciones en noviembre de 2025**. La página devuelve un 500 en vez de decirlo.

No hace falta arreglarlo. Scrappy **no usa la API**, usa los feeds Atom públicos y no necesita credenciales ([ADR-0009](adr/0009-reddit-por-rss.md)). Borra `SCRAPPY_REDDIT_CLIENT_ID` y `_SECRET` de tu `.env` si los tenías: ya no existen.

### Reddit: 429 constantes

Dos causas, por orden:

1. **Tu `SCRAPPY_REDDIT_USER_AGENT` es genérico.** Sin autenticar, Reddit es especialmente estricto. Usa uno que te identifique:
   ```
   windows:scrappy:0.1.0 (by /u/tu_usuario_real)
   ```
2. **Estás consultando demasiados subreddits seguidos.** El límite sin autenticar ronda las 10 peticiones por minuto. Sube `delay_seconds` a 15–20 y baja `subreddits_per_run` a 2 en `config/sources.yaml`. No pierdes cobertura: los subreddits rotan entre ejecuciones.

### Reddit: `la respuesta no es un feed valido`

Reddit está bloqueando la petición y devolviendo su página de error con un 200 engañoso. Revisa el User-Agent y espacia más las peticiones.

Si persiste con una configuración correcta, puede que Reddit haya cerrado también los feeds RSS — estaba anunciado como posible. En ese caso no hay arreglo por nuestra parte.

### X: `HTTP 403`

El esperado. **El tier gratuito de la API de X no permite buscar posts**; el endpoint de búsqueda requiere el tier Basic (~200 USD/mes). No es un fallo de configuración.

Alternativa: `SCRAPPY_X_BACKEND=scrape`, que no cuesta dinero pero incumple los términos de X. Lee [LEGAL.md](LEGAL.md) antes.

### TikTok / Instagram no devuelven nada

Por orden de probabilidad:

1. `SCRAPPY_ENABLE_TOS_RISKY_SOURCES` no está en `true`.
2. Faltan cookies. Instagram no funciona sin ellas.
3. Las cookies caducaron. Duran semanas, no meses.
4. yt-dlp está desactualizado: `pip install -U yt-dlp`.
5. La plataforma cambió algo por dentro. Pasa cada pocas semanas; es el precio de no tener API.

Estas fuentes son frágiles **por diseño de las plataformas**, no por un fallo de Scrappy. Si dependes de que funcionen siempre, usa Reddit.

### Cómo exportar cookies

Con una extensión que exporte en formato **Netscape** (por ejemplo "Get cookies.txt LOCALLY"). Usa una **cuenta secundaria**: esto puede acabar con la cuenta limitada.

---

## Selección de contenido

### Publica poco o nada

```bash
scrappy fetch --dry-run
```

Mira la columna "veredicto". Si todo dice "nota baja", baja `ranking.min_score` a 0.25. Si dice cosas como "demasiado antiguo", sube `SCRAPPY_MAX_AGE_HOURS`. Si apenas hay filas, sube el `budget` de las fuentes o añade subreddits.

### Publica basura

Sube `min_score` a 0.50, añade palabras a `filters.blocked_keywords`, y sobre todo **revisa tu lista de subreddits**. La calidad del canal la determinan las fuentes mucho más que los pesos ([RANKING.md](RANKING.md)).

### Repite contenido

- ¿`SCRAPPY_STATE_BACKEND` está en `memory` o `none`? Esos no recuerdan entre ejecuciones. Ponlo en `sqlite`.
- ¿Se borró `data/scrappy.db`?
- Si son reposts recomprimidos que el hash no pilla, sube `SCRAPPY_PHASH_THRESHOLD` a 8. Con cuidado: valores altos empiezan a juntar cosas distintas.

### Una fuente domina el canal

Baja su `weight` y su `budget` en `sources.yaml`.

---

## Descargas

### `yt-dlp no genero ningun fichero`

El post es privado, se borró, o supera `SCRAPPY_MAX_DOWNLOAD_MB`. Es normal que pase con algunos items; el pipeline los anota y sigue.

Si pasa con **todos**, actualiza yt-dlp:

```bash
pip install -U yt-dlp
```

Es la dependencia que más se mueve, precisamente porque las plataformas cambian.

### `el clip dura Ns y no cabe en 50 MB con calidad aceptable`

Correcto y deseable: comprimir un clip de 10 minutos a 50 MB daría un resultado infame. Baja `SCRAPPY_MAX_DURATION_SECONDS` para que ni se intente.

---

## Ficheros temporales

### `workspace_cleanup_failed` en los logs

Algo impidió borrar un directorio temporal. En Windows suele ser un fichero aún bloqueado.

```bash
scrappy health   # ¿cuántos workspaces activos?
scrappy purge    # bórralos ahora
```

Si se repite, abre una issue con la salud y los logs: es un fallo en la garantía central del proyecto y merece atención.

### ¿Cómo compruebo que no queda nada?

Está explicado paso a paso en [EPHEMERAL_STORAGE.md](EPHEMERAL_STORAGE.md#cómo-comprobarlo-tú-mismo).

---

## Docker

### El contenedor reinicia en bucle

```bash
docker compose logs --tail=50
```

Casi siempre es configuración inválida. `docker compose run --rm scrappy scrappy health` te lo dirá sin el bucle de reinicios.

### `no such file or directory: /app/config/sources.yaml`

No copiaste el ejemplo:

```bash
cp config/sources.example.yaml config/sources.yaml
```

### Se queda sin memoria transcodificando

Sube el tmpfs y el límite en `docker-compose.yml`:

```yaml
tmpfs:
  - /tmp/scrappy:size=1g,mode=1777
deploy:
  resources:
    limits:
      memory: 2g
```

---

## Sigue sin funcionar

Abre una [issue](https://github.com/Ahiram/Scrappy/issues/new/choose) con:

1. La salida de `scrappy health`.
2. Los logs relevantes con `SCRAPPY_LOG_LEVEL=DEBUG`.
3. Qué fuente y qué comando.
4. Sistema operativo y forma de despliegue.

**No pegues tokens ni cookies.** `/config` los redacta por ti, y los logs también.
