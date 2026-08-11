# Despliegue

Tres formas de poner Scrappy a funcionar, de más a menos recomendada.

---

## Requisitos

| | Detalle |
|---|---|
| **Python** | 3.11–3.14. La imagen Docker fija 3.12, que es lo que se prueba en CI. |
| **ffmpeg** | Obligatorio. yt-dlp lo usa para unir video y audio; Scrappy para normalizar clips y extraer frames. |
| **Disco** | ~200 MB para la imagen + unos pocos MB de metadatos. El contenido no ocupa: se borra. |
| **RAM** | 512 MB bastan. En Docker el workspace es un tmpfs, así que 1 GB de límite da margen holgado. |
| **Red** | Salida HTTPS. Sin puertos entrantes: el bot usa polling, no webhooks. |

---

## Opción 1: Docker (recomendada)

Trae ffmpeg dentro, corre sin privilegios, y monta el workspace en RAM.

```bash
git clone https://github.com/Ahiram/Scrappy.git && cd Scrappy
cp .env.example .env
cp config/sources.example.yaml config/sources.yaml
```

Edita `.env` con tu token y tu chat id, y arranca:

```bash
docker compose up -d
```

Comprobar que va bien:

```bash
docker compose logs -f
docker compose exec scrappy scrappy health
```

### Qué hace el compose por ti

| Ajuste | Por qué |
|---|---|
| `tmpfs: /tmp/scrappy` | El workspace vive en RAM: el video nunca toca el disco físico |
| `volumes: scrappy-data:/data` | Único volumen persistente, y solo lleva metadatos de dedup |
| `sources.yaml:ro` | El contenedor no puede modificar tu catálogo |
| `memory: 1g` | Sin techo, un video patológico podría comerse la máquina |
| `no-new-privileges` | Endurecimiento estándar |
| `restart: unless-stopped` | Sobrevive a reinicios del host |
| Rotación de logs | 3 ficheros de 10 MB, no crece sin límite |

### Actualizar

```bash
git pull
docker compose build
docker compose up -d
```

Los metadatos sobreviven: están en un volumen con nombre.

### Cookies

Si usas fuentes que las requieren, descomenta el montaje en `docker-compose.yml`:

```yaml
volumes:
  - ./secrets:/secrets:ro
```

Y en el `.env`:

```bash
SCRAPPY_INSTAGRAM_COOKIES_FILE=/secrets/instagram.cookies.txt
```

`secrets/` está en `.gitignore`. Un fichero de cookies equivale a tu contraseña ([LEGAL.md](LEGAL.md)).

---

## Opción 2: Linux con systemd

Para un VPS o una Raspberry sin Docker.

### Instalación

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv ffmpeg git
sudo useradd --system --create-home --home-dir /opt/scrappy scrappy
sudo -u scrappy git clone https://github.com/Ahiram/Scrappy.git /opt/scrappy/app
cd /opt/scrappy/app
sudo -u scrappy python3.12 -m venv .venv
sudo -u scrappy .venv/bin/pip install -e .
sudo -u scrappy cp .env.example .env
sudo -u scrappy cp config/sources.example.yaml config/sources.yaml
sudo -u scrappy nano .env
sudo chmod 600 .env
```

### La unidad

`/etc/systemd/system/scrappy.service`:

```ini
[Unit]
Description=Scrappy - curador de memes hacia Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=scrappy
Group=scrappy
WorkingDirectory=/opt/scrappy/app
Environment=SCRAPPY_LOG_FORMAT=json
ExecStart=/opt/scrappy/app/.venv/bin/scrappy run
Restart=on-failure
RestartSec=30

# El workspace efimero en RAM: el equivalente al tmpfs de Docker.
PrivateTmp=true

# Endurecimiento. `ReadWritePaths` deja escribir solo donde hace falta:
# la base de metadatos. Ningun otro sitio del sistema es escribible.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/scrappy/app/data
PrivateDevices=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

# `SIGTERM` dispara la purga de workspaces; 30s sobran para terminar
# la publicacion en curso.
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`PrivateTmp=true` es la pieza clave: da al servicio un `/tmp` propio en RAM que systemd destruye al parar. Cumple el mismo papel que el tmpfs de Docker.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scrappy
sudo journalctl -u scrappy -f
```

---

## Opción 3: Windows

Para desarrollo o un PC que esté siempre encendido.

```bash
winget install Gyan.FFmpeg
```

Cierra y reabre la terminal para que el `PATH` se refresque.

```bash
git clone https://github.com/Ahiram/Scrappy.git
cd Scrappy
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
copy config\sources.example.yaml config\sources.yaml
```

Comprueba y prueba antes de dejarlo suelto:

```bash
scrappy health
scrappy fetch --dry-run
```

### Como tarea programada

Al arrancar la sesión, sin ventana:

```bash
schtasks /create /tn "Scrappy" /tr "C:\DEV\Github\Scrappy\.venv\Scripts\scrappy.exe run" /sc onlogon /rl highest
```

Mejor aún: crea un `.vbs` que lance el `.exe` con `WindowStyle 0` para que no aparezca la consola.

**Un aviso sobre Windows:** el borrado de workspaces tiene que lidiar con ficheros bloqueados y con el flag de solo lectura. Está resuelto y probado en CI sobre Windows, pero si ves avisos `workspace_cleanup_failed` en los logs, ejecuta `scrappy purge` y abre una issue.

---

## Después de desplegar

```bash
scrappy health              # ¿ffmpeg, estado, Telegram, fuentes?
scrappy whoami              # ¿el bot accede al canal?
scrappy fetch --dry-run     # ¿la selección tiene buena pinta?
scrappy fetch -n 1          # publica uno de verdad
```

Y desde Telegram, `/health` y `/stats`.

---

## Nota sobre GitHub Actions

Es tentador usar un workflow con `cron` y ahorrarse el servidor, pero **no funciona bien para este caso**: los runners no tienen estado persistente, así que el dedup se pierde entre ejecuciones y el bot repetiría contenido. Se puede apañar con la caché de Actions, pero es frágil.

Si aun así quieres intentarlo, usa `SCRAPPY_STATE_BACKEND=memory` y asume las repeticiones. Un VPS de 3 €/mes o una Raspberry Pi dan mucho mejor resultado.
