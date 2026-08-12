<div align="center">

# Scrappy

**Descubre los mejores videos cortos y memes de internet, los rankea, y los publica en tu chat de Telegram — sin dejar ni un byte de contenido en tu disco.**

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Licencia MIT](https://img.shields.io/badge/licencia-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Checked with mypy](https://img.shields.io/badge/tipos-mypy%20strict-blue.svg)](https://mypy-lang.org/)
[![Docker](https://img.shields.io/badge/docker-listo-2496ED?logo=docker&logoColor=white)](Dockerfile)

</div>

---

## Qué hace

Scrappy consulta varias plataformas, se queda solo con lo mejor de lo que encuentra, lo descarga, comprueba que no lo haya publicado ya, lo envía a tu canal de Telegram **y lo borra**. El contenido vive en tu máquina el tiempo justo de subirlo.

```
Descubrir ──▶ Filtrar ──▶ Rankear ──▶ Descargar ──▶ Deduplicar ──▶ Publicar ──▶ Borrar
                                     └──────── workspace efímero ────────┘
```

| | |
|---|---|
| 🔍 **Ocho fuentes** | Tres funcionan **sin ninguna credencial**: Reddit, Lemmy y Bluesky |
| 🏆 **Ranking normalizado** | 12k upvotes de Reddit no son 12k likes de TikTok: cada item compite contra los de su propia plataforma |
| ⚡ **Detecta lo que explota** | El término de *velocidad* premia lo que se hace viral rápido, no lo que ya lo era ayer |
| 🗑️ **Cero contenido en disco** | Borrado garantizado por `finally`, handler de señales y barrido al arrancar |
| 🔁 **Sin repeticiones** | Deduplicación por id, por SHA-256 y por hash perceptual (detecta reposts recomprimidos) |
| 🤖 **Automático e interactivo** | Scheduler cada N horas *más* comandos `/fetch`, `/stats`, `/health`… |
| 📎 **Atribución obligatoria** | Cada publicación acredita al autor y enlaza al original. No se puede desactivar |
| 🐳 **Docker listo** | ffmpeg incluido, workspace en tmpfs, usuario sin privilegios |

---

## Antes de empezar: lee esto

Este bot **republica trabajo de otras personas**. Las fuentes están en tres niveles:

| Nivel | Fuentes | Qué significa |
|---|---|---|
| ✅ **Abiertas** | Lemmy, Bluesky | API pública sin autenticación, por diseño de la plataforma |
| ✅ **Con clave gratuita** | Reddit *(feeds RSS)*, Imgur, Giphy | Registro autoservicio o ni eso |
| ⚠️ **Detrás de un flag** | YouTube, TikTok, Instagram, X-scrape | **Incumplen los términos** de sus plataformas. Vienen desactivadas y hace falta `SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true` a mano. El proyecto no lo hace por ti |

Sobre Reddit: su registro de aplicaciones **se cerró en noviembre de 2025** y los endpoints `.json` devuelven 403 desde mayo de 2026. Scrappy usa los feeds Atom públicos, que siguen abiertos y no piden credenciales ([ADR-0009](docs/adr/0009-reddit-por-rss.md)).

El contenido pertenece a quien lo creó. Scrappy siempre acredita al autor y enlaza al original, pero eso no equivale a un permiso. **Lee [docs/LEGAL.md](docs/LEGAL.md)** antes de publicar nada fuera de un canal privado.

---

## Instalación rápida

### Con Docker (recomendado)

```bash
git clone https://github.com/Ahiram/Scrappy.git && cd Scrappy
cp .env.example .env && cp config/sources.example.yaml config/sources.yaml
```

Rellena `.env` con tu token de bot y tu chat id, y arranca:

```bash
docker compose up -d
```

### Sin Docker

Necesitas Python 3.11+ y **ffmpeg** en el `PATH`.

```bash
python -m venv .venv && .venv/bin/activate && pip install -e ".[dev]"
```

En Windows: `.venv\Scripts\activate` y `winget install Gyan.FFmpeg`.

---

## Configuración mínima

Solo hacen falta cuatro valores para empezar a publicar desde Reddit:

| Variable | De dónde sale |
|---|---|
| `SCRAPPY_TELEGRAM_BOT_TOKEN` | Habla con [@BotFather](https://t.me/BotFather) y usa `/newbot` |
| `SCRAPPY_TELEGRAM_TARGET_CHAT_ID` | Añade el bot al canal como administrador y ejecuta `scrappy whoami` |
| `SCRAPPY_TELEGRAM_ADMIN_IDS` | Tu id numérico de Telegram, para poder usar los comandos |
| `SCRAPPY_REDDIT_USER_AGENT` | Solo tu usuario de Reddit: `windows:scrappy:0.1.0 (by /u/tu_usuario)` |

**Reddit no necesita credenciales.** Scrappy usa sus feeds Atom públicos, no la API — el registro de aplicaciones se cerró en noviembre de 2025 y los endpoints `.json` devuelven 403 desde mayo de 2026. Lo único obligatorio es identificarte en el User-Agent: con uno genérico, Reddit responde 429. El porqué completo está en [ADR-0009](docs/adr/0009-reddit-por-rss.md).

La referencia completa de las ~30 variables está en **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

---

## Uso

Empieza siempre por un ensayo en seco. No descarga ni publica nada, solo te enseña qué habría elegido y por qué:

```bash
scrappy fetch --dry-run
```

```
                 Candidatos evaluados (no se descargo nada)
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ score ┃ fuente ┃ veredicto    ┃ engagement ┃ titulo                     ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 0.891 │ reddit │ SELECCIONADO │     48,213 │ He didn't expect that      │
│ 0.774 │ reddit │ SELECCIONADO │     22,904 │ Perfectly timed            │
│ 0.301 │ reddit │ nota baja    │      1,204 │ Meh                        │
│ 0.000 │ reddit │ demasiado…   │        890 │ 4 hour compilation         │
└───────┴────────┴──────────────┴────────────┴────────────────────────────┘
```

Cuando la selección te convenza:

```bash
scrappy health                       # ffmpeg, estado, Telegram, fuentes
scrappy fetch --source reddit -n 2   # publica 2 items ahora
scrappy run                          # bot + scheduler (lo que corre en Docker)
```

### Comandos del bot en Telegram

| Comando | Qué hace |
|---|---|
| `/fetch [fuente] [n]` | Dispara el pipeline al momento |
| `/sources` | Estado de cada fuente |
| `/stats [días]` | Qué se ha publicado y desde dónde |
| `/pause` · `/resume` | Para y reanuda el scheduler |
| `/config` | Configuración efectiva, sin secretos |
| `/health` | Diagnóstico, incluido "el disco está limpio" |
| `/purge` | Borra los temporales ahora mismo |

Solo responden a los ids de `SCRAPPY_TELEGRAM_ADMIN_IDS`.

---

## La garantía de "nada en disco"

Es la restricción de diseño central del proyecto, no un detalle de implementación:

- Las **imágenes y GIFs** nunca tocan el disco: van en memoria de la descarga al envío.
- Los **videos** sí necesitan un fichero temporal (ffmpeg necesita hacer *seeking*), pero viven dentro de un `EphemeralWorkspace` que se borra en su `finally` — retorno normal, excepción o cancelación, da igual.
- En Docker ese workspace es un **tmpfs**: está en RAM, no llega a escribirse en el disco físico.
- Un handler de `SIGTERM`/`SIGINT` purga lo que hubiera vivo antes de que el proceso muera.
- Al arrancar se **barren huérfanos** por si un crash anterior dejó restos.
- Lo único que persiste son ~200 bytes de metadatos por item para no repetir contenido — y se puede desactivar con `SCRAPPY_STATE_BACKEND=memory` o `=none`.

Detalle completo y cómo verificarlo: **[docs/EPHEMERAL_STORAGE.md](docs/EPHEMERAL_STORAGE.md)**.

---

## Documentación

| Documento | Para qué |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Cómo encaja todo, con diagramas |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Todas las variables y claves del YAML |
| [RANKING.md](docs/RANKING.md) | La fórmula de "lo mejor" y cómo calibrarla |
| [SOURCES.md](docs/SOURCES.md) | Añadir una plataforma nueva |
| [EPHEMERAL_STORAGE.md](docs/EPHEMERAL_STORAGE.md) | La garantía de cero-disco |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, VPS, systemd, Windows |
| [OPERATIONS.md](docs/OPERATIONS.md) | Día a día: logs, backups, actualizaciones |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Cuando algo no va |
| [LEGAL.md](docs/LEGAL.md) | ToS, copyright, uso responsable |
| [adr/](docs/adr/) | Por qué cada decisión técnica es como es |

---

## Desarrollo

```bash
ruff check . && ruff format --check . && mypy src && pytest --cov=src/scrappy
```

Los tests no tocan la red: las llamadas HTTP van con `respx` y Telegram con un doble de prueba. Hay un test dedicado a comprobar que **no queda ningún fichero** tras un run, incluido uno que falla a mitad.

Cómo contribuir: [CONTRIBUTING.md](CONTRIBUTING.md). Vulnerabilidades: [SECURITY.md](SECURITY.md).

---

## Licencia

Código bajo [MIT](LICENSE). El contenido que el bot descubre **no** es tuyo ni mío: es de quien lo creó.
