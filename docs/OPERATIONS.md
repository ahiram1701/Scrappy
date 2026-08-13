# Operación diaria

Qué mirar, cada cuánto, y qué hacer cuando algo cambia.

---

## Cuándo corre Scrappy

La pregunta que más sorprende, y la que explica casi todos los «dejó de publicar de repente».

> **Scrappy publica mientras hay un proceso suyo vivo, y ni un minuto más.**

No hay ningún servicio de fondo esperando, ni nada agendado en la nube. Si el proceso muere —cierras la ventana, apagas el equipo, se cae la sesión— deja de publicar hasta que vuelva a arrancar. No se acumulan rondas pendientes: se pierden.

### Las cuatro formas de tenerlo corriendo

| Forma | Cómo se arranca | Corre… | Cuándo tiene sentido |
|---|---|---|---|
| **La ventana de la TUI** | Doble clic en `Scrappy.bat`, y «Arrancar» en el Panel | Mientras la ventana esté abierta | Explorar, calibrar, publicar a mano. **Cerrar la ventana es dejar de publicar** |
| **Tarea al iniciar sesión** | Panel → Arranque automático → Activar | Desde que inicias sesión hasta que la cierras | Un equipo de escritorio que usas a diario. Sin ventana y sin acordarte |
| **`scrappy run` en una terminal** | El comando, y dejarla abierta | Mientras la terminal viva | Ver los logs en directo mientras se depura algo |
| **Docker o systemd** | `docker compose up -d` | Siempre, y se reinicia solo si se cae | Que publique aunque tu equipo esté apagado. Ver [DEPLOYMENT.md](DEPLOYMENT.md) |

### Dos preguntas distintas

Que Scrappy esté corriendo y que **publique solo** no es lo mismo. Hacen falta las dos cosas:

1. **Un proceso vivo**, según la tabla de arriba.
2. **`SCRAPPY_SCHEDULE_ENABLED=true`**, que es como viene.

Con la segunda puesta, el scheduler arranca solo: lo hacen tanto `scrappy run` como la TUI al abrirse. No hay que acordarse de nada — salvo que falte configurar Telegram, en cuyo caso no arranca y el panel lo dice, porque publicar sin bot solo produciría un fallo cada N horas.

Con proceso pero con el scheduler desactivado, el bot responde a `/fetch` pero no publica por su cuenta. Con el scheduler activado pero sin proceso, no pasa nada en absoluto: no hay nadie para ejecutarlo.

`/start` te dice las dos cosas: si contesta, hay proceso; y su última línea dice si publicará solo, cada cuánto y en qué zona horaria.

### Si tienes dos a la vez

Con el arranque automático activo **y** el scheduler de la ventana en marcha hay dos procesos que pueden publicar. **No salen publicaciones repetidas** —la deduplicación lo impide— pero sí puede publicarse más de lo que esperas, porque son dos rondas independientes. El Panel avisa cuando detecta esa situación.

---

## Comprobaciones rutinarias

| Cada… | Qué | Cómo |
|---|---|---|
| Diario | ¿Publica? | Mira el canal, o `/stats 1` |
| Semanal | ¿Fuentes sanas? | `/sources` |
| Semanal | ¿Calidad? | Lee lo publicado con ojo crítico |
| Mensual | Actualizar yt-dlp | Reconstruir la imagen o `pip install -U yt-dlp` |
| Mensual | Diagnóstico completo | `/health` |
| Trimestral | Recalibrar | `scrappy fetch --dry-run` y revisar pesos |

La única que de verdad no conviene saltarse es **actualizar yt-dlp**. Es la dependencia que más se mueve, porque las plataformas cambian por dentro constantemente.

---

## Logs

En producción salen en JSON, una línea por evento:

```bash
docker compose logs -f scrappy                    # Docker
sudo journalctl -u scrappy -f                     # systemd
docker compose logs scrappy | jq 'select(.level=="warning")'
```

Los tokens y cookies se redactan automáticamente antes de escribir nada, así que un log se puede compartir sin miedo.

### Eventos que importan

| Evento | Significa |
|---|---|
| `run_finished` | Resumen de una ejecución. El pulso normal. |
| `published` | Un item salió al canal. |
| `duplicate` | Dedup funcionando. Verlo es buena señal. |
| `source_failed` | Una fuente falló esta ronda. Si es puntual, no pasa nada. |
| `source_rate_limited` | Te estás pasando de peticiones. |
| `item_failed` | Un item concreto no salió. Normal en pequeñas dosis. |
| `ffmpeg_missing` | **Los videos van a fallar todos.** |
| `workspace_cleanup_failed` | **Puede haber quedado un fichero.** Investígalo. |
| `no_admins_configured` | Nadie puede usar los comandos. |

Los dos en negrita merecen atención inmediata; el resto son informativos.

---

## Qué es normal y qué no

Una ejecución sana con Reddit se parece a esto:

```
run descubiertos=54 published=5 duplicate=31 filtered=12 low_score=6 en 47.3s
```

| Señal | Interpretación |
|---|---|
| `duplicate` alto | **Bien.** Significa que el catálogo se renueva más despacio que el intervalo. |
| `published` = 0 varias veces seguidas | Baja `min_score` o sube el `budget` |
| `filtered` casi igual a `descubiertos` | Filtros demasiado estrictos, o subreddits que no encajan |
| `download_failed` alto | yt-dlp desactualizado, o una plataforma cambió |
| Duración > 5 min | Clips muy largos, o red lenta |

Un `duplicate` alto **no es un problema**: es el sistema evitando que repitas. Si publicas cada 3 horas y tus subreddits producen 10 posts buenos al día, es matemáticamente inevitable.

---

## Copias de seguridad

Solo hay un fichero que respaldar, y es diminuto:

```bash
# Docker
docker compose exec scrappy sqlite3 /data/scrappy.db ".backup /tmp/backup.db"
docker compose cp scrappy:/tmp/backup.db ./scrappy-backup.db

# Instalación normal
sqlite3 data/scrappy.db ".backup data/backup.db"
```

Usa `.backup` de sqlite3 y no `cp`: con WAL activado, copiar el fichero a pelo puede dar una copia inconsistente.

**Qué se pierde si no lo respaldas:** el historial de deduplicación. El bot volvería a publicar cosas que ya publicó. No es una catástrofe, pero es molesto.

**Qué no hace falta respaldar:** el contenido, porque no está ahí. Está en Telegram.

---

## Actualizar

```bash
# Docker
git pull && docker compose build && docker compose up -d

# systemd
cd /opt/scrappy/app
sudo -u scrappy git pull
sudo -u scrappy .venv/bin/pip install -e .
sudo systemctl restart scrappy
```

Los metadatos sobreviven: las migraciones de esquema se aplican solas al arrancar, siguiendo `PRAGMA user_version`.

### Solo yt-dlp

Cuando una fuente empieza a fallar, lo primero:

```bash
docker compose exec scrappy pip install -U yt-dlp   # temporal, hasta el próximo build
```

Para que persista, reconstruye la imagen.

---

## Ajustes en caliente

Estos surten efecto sin reiniciar, porque `sources.yaml` se **relee del disco al empezar cada ronda**:

- Añadir o quitar subreddits, hashtags o cuentas
- Cambiar pesos, penalizaciones y `min_score`
- Añadir palabras o autores a la lista de bloqueo

Los del `.env` no, porque se leen una sola vez al arrancar y desde ahí se reparten por los adapters, el cliente HTTP y el backend de estado:

- Cualquier credencial
- `STATE_BACKEND`, `WORKSPACE_ROOT`, `TIMEZONE`
- El intervalo del scheduler
- Activar o desactivar fuentes

Para aplicarlos **no hace falta reiniciar el proceso**: en el Panel de la TUI, **Recargar configuración** (o `R`) cierra la aplicación interna y la vuelve a montar con los ajustes releídos, sin perder la ventana. Guardar en la pantalla de Configuración ya lo hace por su cuenta.

Comprueba con `/config` qué está usando de verdad: si un cambio no aparece ahí, no ha llegado al proceso.

Fuera de la TUI —Docker, systemd, `scrappy run`— sí hay que reiniciar el proceso.

---

## Pausar

Desde Telegram, para el scheduler dejando los comandos activos:

```
/pause
/resume
```

Es lo que quieres si te vas de vacaciones o si estás recalibrando y no quieres que publique mientras tanto. `/fetch` sigue funcionando en pausa.

---

## Coste de recursos

Con la configuración por defecto (5 items cada 3 horas, solo Reddit):

| Recurso | Consumo |
|---|---|
| CPU | Casi cero en reposo; picos de 30–60 s al transcodificar |
| RAM | ~150 MB en reposo; hasta ~400 MB con un video en vuelo |
| Red | ~1–2 GB al mes de descarga, otro tanto de subida |
| Disco | ~200 MB de imagen + ~3 MB al año de metadatos |

Cabe de sobra en un VPS de los baratos o en una Raspberry Pi 4.

---

## Cambios en las plataformas

Que una fuente sin API deje de funcionar de un día para otro no es un fallo de Scrappy: es lo que pasa cuando dependes de algo que no es una API. Cuando ocurra:

1. `pip install -U yt-dlp` — resuelve la mayoría de los casos.
2. Comprueba si las cookies caducaron.
3. Mira si hay una issue abierta en [yt-dlp](https://github.com/yt-dlp/yt-dlp/issues).
4. Desactiva esa fuente mientras tanto: las demás siguen funcionando.

Reddit, al usar API oficial, es notablemente más estable. Es la razón por la que se recomienda como núcleo.
