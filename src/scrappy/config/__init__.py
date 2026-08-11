"""Configuracion en dos capas: secretos por entorno, catalogo por YAML.

- `settings.py`  lee variables de entorno / `.env`. Ahi viven los secretos y las
  decisiones de infraestructura (tokens, backend de estado, limites).
- `loader.py`    lee `config/sources.yaml`. Ahi vive el catalogo editable: que
  subreddits, que hashtags, que pesos de ranking.

La separacion es intencionada: el YAML se puede versionar y compartir, el `.env`
jamas. Ver `docs/CONFIGURATION.md`.
"""

from scrappy.config.loader import SourcesConfig, load_sources_config
from scrappy.config.settings import Settings, get_settings

__all__ = ["Settings", "SourcesConfig", "get_settings", "load_sources_config"]
