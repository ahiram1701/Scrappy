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

### Reddit: `credenciales rechazadas`

La app tiene que ser de tipo **script** en [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps). El `client_id` es la cadena corta **debajo** del nombre de la app, no el nombre.

### Reddit: 429 constantes

Tu `SCRAPPY_REDDIT_USER_AGENT` es genérico. Reddit lo penaliza. Usa algo único:

```
linux:scrappy:0.1.0 (by /u/tu_usuario_real)
```

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
