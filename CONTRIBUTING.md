# Contribuir a Scrappy

Gracias por querer echar una mano. Este documento explica cómo trabajar en el proyecto y qué se espera de un cambio.

---

## Empezar

```bash
git clone https://github.com/Ahiram/Scrappy.git && cd Scrappy
make setup            # crea el venv e instala todo
cp .env.example .env
cp config/sources.example.yaml config/sources.yaml
```

Necesitas **ffmpeg** en el `PATH`. En Windows: `winget install Gyan.FFmpeg`.

Comprueba que el entorno está bien:

```bash
make check            # ruff + mypy + pytest
```

Si no tienes `make` (Windows sin Git Bash):

```bash
.venv\Scripts\ruff check .
.venv\Scripts\mypy src
.venv\Scripts\python -m pytest
```

---

## El flujo

1. Abre una issue antes de ponerte con algo grande. Ahorra trabajo en ambas direcciones.
2. Rama desde `main`: `git switch -c mi-cambio`
3. Escribe el código **y los tests**.
4. `make check` en verde.
5. Actualiza la documentación afectada.
6. Abre el PR rellenando la plantilla.

---

## Lo que se espera del código

### Comentarios que expliquen el porqué

El *qué* ya lo dice el código. Lo que se pierde es el *porqué*:

```python
# Mal: repite lo que ya se ve
# Incrementa el contador
counter += 1

# Bien: explica una decisión no obvia
# Se autentica ANTES del bucle a proposito. Si las credenciales son
# invalidas, el error debe tumbar la fuente entera de inmediato en vez
# de reintentarse una vez por subreddit.
await self._access_token()
```

Si un número tiene un valor concreto, di de dónde sale. `_SIZE_SAFETY_MARGIN = 0.92` sin explicación es magia; con un comentario de una línea es una decisión.

### Tipos estrictos

`mypy --strict` pasa sobre `src/`. Sin `Any` salvo en fronteras con librerías que no tipan (yt-dlp, respuestas JSON crudas).

### Errores accionables

Un mensaje de error tiene que decirle a alguien qué hacer:

```python
# Mal
raise ConfigError("configuracion invalida")

# Bien
raise DependencyMissingError(
    "ffmpeg no esta en el PATH y es imprescindible...\n"
    "  Windows : winget install Gyan.FFmpeg\n"
    "  Debian  : sudo apt install ffmpeg"
)
```

### Estilo

Ruff se encarga: 100 columnas, comillas dobles, imports ordenados. `make format` lo arregla.

Los identificadores y los mensajes de log van en inglés (`published`, `source_failed`); los comentarios, docstrings y textos de usuario, en español. Los comentarios del código evitan tildes por compatibilidad de codificación; la documentación en Markdown sí las lleva.

---

## Tests

- **Nada de red.** HTTP va con `respx`, Telegram con dobles de prueba.
- **Payloads reales recortados**, no inventados. La mitad de los bugs de un adapter están en campos que la API devuelve distinto a como imaginabas.
- **Nombres que digan qué se verifica**: `test_las_escalas_de_cada_plataforma_no_se_mezclan`, no `test_scorer_2`.
- **Un docstring cuando el test verifica algo sutil**, explicando qué propiedad se está protegiendo.

Cobertura mínima del 80 % en `ranking/`, `storage/` y `delivery/`.

### Si tocas la descarga o el ciclo de vida de los medios

Presta atención especial. Esa parte sostiene la garantía central del proyecto ([EPHEMERAL_STORAGE.md](docs/EPHEMERAL_STORAGE.md)):

- Todo fichero temporal nace dentro de un `EphemeralWorkspace`.
- Los tests `test_no_queda_ningun_fichero_*` tienen que seguir pasando, y son tres: camino feliz, fallo de descarga y fallo de publicación.
- Si añades una ruta de código nueva que escriba a disco, añade un test que verifique que también se limpia.

Un PR que rompa esos tests no se acepta, por buena que sea la funcionalidad.

---

## Añadir una fuente

Guía completa en [docs/SOURCES.md](docs/SOURCES.md). Los tres puntos que más se olvidan:

1. **`permalink` y `author` siempre rellenos.** Son la atribución, y no es opcional.
2. **`requires_tos_ack` según la realidad legal.** Si la plataforma no ofrece API pública para este uso, va a `True` y se documenta en [LEGAL.md](docs/LEGAL.md).
3. **No normalices `engagement` tú.** El scorer lo convierte a percentil; escalarlo a mano rompe justo el mecanismo que hace comparables las plataformas.

---

## Commits

Formato [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(sources): anade adapter de Mastodon
fix(workspace): reintenta el borrado en ficheros bloqueados de Windows
docs(ranking): explica por que se normaliza contra el p90
test(pipeline): cubre el fallo de publicacion a mitad de lote
```

Tipos: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

---

## Seguridad

No abras una issue pública para una vulnerabilidad. Sigue [SECURITY.md](SECURITY.md).

Y en cualquier caso: **nunca incluyas tokens ni cookies** en issues, PRs o logs.

---

## Convivencia

Se aplica el [Código de conducta](CODE_OF_CONDUCT.md). En resumen: trata bien a la gente.
