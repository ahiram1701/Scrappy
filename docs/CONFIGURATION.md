# Configuración

Scrappy se configura en dos capas, y la separación es intencionada:

| Capa | Fichero | Qué va ahí | ¿Se versiona? |
|---|---|---|---|
| **Entorno** | `.env` | Secretos e infraestructura: tokens, límites, backend de estado | **Nunca** |
| **Catálogo** | `config/sources.yaml` | Qué se busca y cuánto pesa cada cosa | Sí, si quieres |

Plantillas: [`.env.example`](../.env.example) y [`config/sources.example.yaml`](../config/sources.example.yaml).

---

## Variables de entorno

Todas llevan el prefijo `SCRAPPY_`. Se leen de `.env` o de variables reales del sistema; estas últimas tienen prioridad.

### Telegram

| Variable | Defecto | Descripción |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | **Obligatorio.** Token de [@BotFather](https://t.me/BotFather) (`/newbot`). |
| `TELEGRAM_TARGET_CHAT_ID` | — | **Obligatorio.** Chat, grupo o canal destino. Canales y supergrupos tienen id negativo (`-100…`). |
| `TELEGRAM_ADMIN_IDS` | *(vacío)* | Ids separados por coma que pueden usar los comandos. **Vacío = nadie**, deliberadamente. |

**Cómo obtener el chat id:** añade el bot al canal como administrador y ejecuta `scrappy whoami`. Si el bot no puede acceder, te lo dirá.

### Almacenamiento

| Variable | Defecto | Descripción |
|---|---|---|
| `STATE_BACKEND` | `sqlite` | `sqlite`, `memory` o `none`. Ver [EPHEMERAL_STORAGE.md](EPHEMERAL_STORAGE.md). |
| `STATE_DB_PATH` | `data/scrappy.db` | Solo para `sqlite`. Únicamente metadatos, jamás medios. |
| `WORKSPACE_ROOT` | *(temp del sistema)* | Raíz de los workspaces efímeros. En Docker es un tmpfs. |
| `MAX_DOWNLOAD_MB` | `60` | Aborta la descarga por encima de esto. El límite real de subida es el menor entre este valor y los 50 MB de Telegram. |

### Scheduler

| Variable | Defecto | Descripción |
|---|---|---|
| `SCHEDULE_ENABLED` | `true` | Si `false`, solo funciona `/fetch`. |
| `SCHEDULE_INTERVAL_MINUTES` | `180` | Cada cuánto se ejecuta el pipeline. |
| `ITEMS_PER_RUN` | `5` | Máximo de publicaciones por ejecución. |

Con los valores por defecto: 5 memes cada 3 horas, unos 40 al día.

### Reddit

**No necesita credenciales.** Scrappy usa los feeds Atom públicos porque el registro de aplicaciones se cerró en noviembre de 2025 y los endpoints `.json` devuelven 403 desde mayo de 2026 ([ADR-0009](adr/0009-reddit-por-rss.md)).

| Variable | Defecto | Descripción |
|---|---|---|
| `REDDIT_ENABLED` | `true` | |
| `REDDIT_USER_AGENT` | genérico | **Cámbialo, es obligatorio.** Con uno genérico Reddit responde 429. Formato: `plataforma:scrappy:0.1.0 (by /u/tu_usuario)`. `scrappy sources` avisa si no te identifica. |

Y en `sources.yaml`, dos claves propias de esta fuente por el rate limit:

| Clave | Defecto | Descripción |
|---|---|---|
| `subreddits_per_run` | `3` | Cuántos subreddits consultar por ronda. Se rotan, así que la lista entera se cubre en varias ejecuciones. |
| `delay_seconds` | `12` | Espera entre subreddits. Bajarlo provoca 429. |

### Lemmy y Bluesky

Ninguna de las dos pide credenciales: sus APIs son públicas sin autenticación.

| Variable | Defecto | Descripción |
|---|---|---|
| `LEMMY_ENABLED` | `true` | La instancia y las comunidades se configuran en `sources.yaml`. |
| `BLUESKY_ENABLED` | `true` | Las consultas se configuran en `sources.yaml`. |

Claves propias en `sources.yaml`:

| Fuente | Clave | Defecto | Descripción |
|---|---|---|---|
| lemmy | `instance` | `https://lemmy.world` | Cualquier instancia de Lemmy sirve. |
| lemmy | `sort` | `TopDay` | `TopHour`, `TopSixHour`, `TopDay`, `TopWeek`, `Hot`, `Active`… |
| lemmy | `communities` | — | Formato `nombre@instancia`. Vacío = portada de la instancia. |
| bluesky | `window_hours` | `24` | **Importante.** Sin acotar, `sort=top` devuelve lo más votado de *siempre*. |
| bluesky | `queries` | — | Términos de búsqueda. |

### Imgur y Giphy

Ambas mantienen el registro autoservicio que Reddit cerró.

| Variable | Defecto | Descripción |
|---|---|---|
| `IMGUR_ENABLED` | `false` | |
| `IMGUR_CLIENT_ID` | — | De [api.imgur.com/oauth2/addclient](https://api.imgur.com/oauth2/addclient), opción *Anonymous usage*. El secreto **no** hace falta. ~12.500 peticiones/día. |
| `GIPHY_ENABLED` | `false` | |
| `GIPHY_API_KEY` | — | De [developers.giphy.com](https://developers.giphy.com). La clave beta da 100 llamadas/hora, de sobra para este uso. |

Giphy no expone ningún contador de popularidad, así que el ranking usa la posición en `trending` — que ya es un ranking hecho por ellos.

### Fuentes con riesgo de ToS

> Léete [LEGAL.md](LEGAL.md) antes de tocar esta sección.

| Variable | Defecto | Descripción |
|---|---|---|
| `ENABLE_TOS_RISKY_SOURCES` | `false` | **Interruptor maestro.** Sin esto en `true`, YouTube, X-scrape, TikTok e Instagram no arrancan aunque estén habilitadas. |
| `YOUTUBE_ENABLED` | `false` | Shorts vía yt-dlp, sin clave ni cookies. Va tras el flag porque descargar de YouTube también incumple sus términos, aunque el riesgo práctico sea mucho menor. |
| `X_ENABLED` | `false` | |
| `X_BACKEND` | `api` | `api` (oficial, requiere tier de pago para buscar) o `scrape` (yt-dlp, contra ToS). |
| `X_BEARER_TOKEN` | — | Solo para el backend `api`. |
| `X_COOKIES_FILE` | — | Solo para `scrape`. Formato Netscape. |
| `TIKTOK_ENABLED` | `false` | |
| `TIKTOK_COOKIES_FILE` | — | Opcional, pero mejora mucho la fiabilidad. |
| `INSTAGRAM_ENABLED` | `false` | |
| `INSTAGRAM_COOKIES_FILE` | — | **Obligatorio** para Instagram. |

### Contenido y filtros

| Variable | Defecto | Descripción |
|---|---|---|
| `ALLOW_NSFW` | `false` | |
| `MIN_DURATION_SECONDS` | `1` | |
| `MAX_DURATION_SECONDS` | `180` | Por encima se descarta. |
| `MAX_AGE_HOURS` | `48` | Ignora posts más viejos. |
| `PHASH_THRESHOLD` | `6` | Distancia de Hamming máxima para considerar dos medios el mismo. Subirlo detecta más reposts pero puede juntar cosas distintas. |

### Observabilidad

| Variable | Defecto | Descripción |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FORMAT` | `console` | `console` para desarrollo, `json` para producción. La imagen Docker usa `json`. |
| `SOURCES_CONFIG_PATH` | `config/sources.yaml` | |

---

## El fichero `sources.yaml`

Si no existe, se usan los valores por defecto: el proyecto arranca recién clonado sin copiar nada.

### `ranking`

Explicado en detalle en [RANKING.md](RANKING.md).

```yaml
ranking:
  weights:
    engagement: 0.50   # cuánto pesa destacar dentro de su lote
    velocity: 0.35     # cuánto pesa hacerse viral rápido
    source: 0.15       # tu preferencia manual por plataforma
  penalties:
    too_long: 0.20
    too_short: 0.10
    no_thumbnail: 0.05
    low_comment_ratio: 0.10
  min_comment_ratio: 0.002
  min_score: 0.35      # por debajo, ni se descarga
```

### `sources`

Claves comunes a todas: `weight` (0–1) y `budget` (candidatos a pedir antes de filtrar).

```yaml
sources:
  reddit:
    weight: 0.9
    budget: 60
    listing: top          # top | hot | rising
    time_filter: day      # hour | day | week (solo con `top`)
    subreddits: [memes, dankmemes, PerfectTiming]
    min_score: 500        # upvotes mínimos, sea cual sea su percentil

  x:
    weight: 0.7
    budget: 40
    queries:              # backend `api`, sintaxis de X API v2
      - "(funny OR meme) has:videos -is:retweet lang:es min_faves:2000"
    accounts: [Memes]     # backend `scrape`

  tiktok:
    weight: 0.6
    budget: 30
    hashtags: [humor, memes]
    accounts: []
    min_play_count: 100000

  instagram:
    weight: 0.6
    budget: 25
    hashtags: [memes, reels]
    accounts: []
```

**Sobre `budget`:** subirlo cuesta muy poco (solo son metadatos) y mejora la selección, porque el percentil se calcula sobre una muestra mayor. Suele ser mejor palanca que tocar los pesos.

### `filters`

```yaml
filters:
  blocked_keywords: [gore, nsfw, onlyfans, crypto]
  blocked_authors: []
  allowed_languages: []   # vacío = no filtrar por idioma
```

Las palabras se buscan en el título, sin distinguir mayúsculas. Los autores se comparan sin la `@`.

### `delivery`

```yaml
delivery:
  show_score: false           # útil para calibrar
  show_source_badge: true     # emoji identificando la plataforma
  silent_notifications: false
  delay_between_posts: 4      # segundos entre publicaciones
```

**No hay opción para desactivar la atribución.** Republicar trabajo ajeno sin acreditar al autor no es negociable ([LEGAL.md](LEGAL.md)).

---

## Configuraciones de ejemplo

### Mínima: solo Reddit, canal privado

```bash
SCRAPPY_TELEGRAM_BOT_TOKEN=123456:AAA...
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=-1001234567890
SCRAPPY_TELEGRAM_ADMIN_IDS=987654321
SCRAPPY_REDDIT_CLIENT_ID=...
SCRAPPY_REDDIT_CLIENT_SECRET=...
SCRAPPY_REDDIT_USER_AGENT=linux:scrappy:0.1.0 (by /u/tu_usuario)
```

### Máxima privacidad: cero rastro en la máquina

```bash
SCRAPPY_STATE_BACKEND=memory   # ni siquiera los hashes tocan el disco
SCRAPPY_LOG_LEVEL=WARNING
```

A cambio, el bot puede repetir contenido entre ejecuciones.

### Canal de alto volumen

```bash
SCRAPPY_SCHEDULE_INTERVAL_MINUTES=60
SCRAPPY_ITEMS_PER_RUN=10
SCRAPPY_MAX_AGE_HOURS=24
```

Y sube el `budget` de cada fuente en el YAML: con más volumen necesitas más candidatos para que la selección siga siendo exigente.

---

## Comprobar la configuración

```bash
scrappy health     # ffmpeg, estado, Telegram, fuentes
scrappy sources    # detalle de cada fuente y qué le falta
scrappy whoami     # identidad del bot y acceso al chat destino
```

Y desde Telegram, `/config` vuelca la configuración efectiva con los secretos ya redactados.

Si algo del `.env` no valida, el arranque falla con un mensaje que dice exactamente qué variable y por qué. Es intencionado: mejor no arrancar que arrancar mal.
