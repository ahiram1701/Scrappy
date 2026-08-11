# =============================================================================
# Scrappy -- imagen de produccion
#
# Dos etapas: una construye las dependencias, la otra solo lleva lo necesario
# para ejecutar. Asi la imagen final no arrastra compiladores ni cabeceras.
#
# Python 3.12 y no 3.14: es la version sobre la que se prueba el proyecto en CI
# y para la que todas las dependencias tienen ruedas precompiladas.
# =============================================================================

# -----------------------------------------------------------------------------
# Etapa 1: dependencias
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Solo hace falta un compilador durante la construccion; no viaja a la imagen final.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Se copia primero lo que define las dependencias: mientras no cambie,
# Docker reutiliza la cache aunque cambie el codigo.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# -----------------------------------------------------------------------------
# Etapa 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Scrappy" \
      org.opencontainers.image.description="Bot que cura videos cortos y memes hacia Telegram sin dejar contenido en disco" \
      org.opencontainers.image.source="https://github.com/Ahiram/Scrappy" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # El workspace efimero apunta a /tmp, que docker-compose monta como tmpfs.
    # El video vive en RAM y no llega a tocar el disco fisico.
    SCRAPPY_WORKSPACE_ROOT=/tmp/scrappy \
    SCRAPPY_STATE_DB_PATH=/data/scrappy.db \
    SCRAPPY_SOURCES_CONFIG_PATH=/app/config/sources.yaml \
    SCRAPPY_LOG_FORMAT=json

# ffmpeg y ffprobe son imprescindibles: yt-dlp los usa para unir video y audio,
# y Scrappy para normalizar los clips y extraer frames para el hash perceptual.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios: el bot descarga ficheros de internet, no hay motivo
# para que nada de eso corra como root.
RUN groupadd --gid 1000 scrappy \
    && useradd --uid 1000 --gid scrappy --create-home scrappy \
    && mkdir -p /data /tmp/scrappy \
    && chown -R scrappy:scrappy /data /tmp/scrappy

COPY --from=builder /opt/venv /opt/venv
COPY --chown=scrappy:scrappy config/sources.example.yaml /app/config/sources.example.yaml

WORKDIR /app
USER scrappy

# Comprueba ffmpeg, el backend de estado y que no queden ficheros temporales.
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=3 \
    CMD ["scrappy", "health"]

ENTRYPOINT ["scrappy"]
CMD ["run"]
