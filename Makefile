# =============================================================================
# Scrappy -- atajos de desarrollo
#
# `make help` lista los objetivos disponibles.
# =============================================================================

PYTHON ?= python
VENV   ?= .venv

ifeq ($(OS),Windows_NT)
	BIN := $(VENV)/Scripts
else
	BIN := $(VENV)/bin
endif

.DEFAULT_GOAL := help
.PHONY: help setup lint format typecheck test cov check run dry-run health docker-build docker-up docker-down docker-logs clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Entorno
# -----------------------------------------------------------------------------
setup: ## Crea el entorno virtual e instala todo
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"
	@echo "Listo. Copia .env.example a .env y config/sources.example.yaml a config/sources.yaml"

# -----------------------------------------------------------------------------
# Calidad
# -----------------------------------------------------------------------------
lint: ## Comprueba estilo y errores
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Aplica el formateo
	$(BIN)/ruff check . --fix
	$(BIN)/ruff format .

typecheck: ## Comprueba los tipos
	$(BIN)/mypy src

test: ## Ejecuta los tests
	$(BIN)/python -m pytest

cov: ## Tests con informe de cobertura
	$(BIN)/python -m pytest --cov=src/scrappy --cov-report=term-missing --cov-report=html
	@echo "Informe HTML en htmlcov/index.html"

check: lint typecheck test ## Todo lo anterior, que es lo que corre en CI

# -----------------------------------------------------------------------------
# Ejecucion
# -----------------------------------------------------------------------------
dry-run: ## Rankea sin descargar ni publicar nada
	$(BIN)/scrappy fetch --dry-run

health: ## Diagnostico del sistema
	$(BIN)/scrappy health

run: ## Arranca el bot y el scheduler
	$(BIN)/scrappy run

# -----------------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------------
docker-build: ## Construye la imagen
	docker compose build

docker-up: ## Arranca el contenedor en segundo plano
	docker compose up -d

docker-down: ## Para el contenedor
	docker compose down

docker-logs: ## Sigue los logs del contenedor
	docker compose logs -f

# -----------------------------------------------------------------------------
# Limpieza
# -----------------------------------------------------------------------------
clean: ## Borra artefactos de construccion y cachés
	$(BIN)/scrappy purge || true
	rm -rf build dist *.egg-info htmlcov .coverage
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
