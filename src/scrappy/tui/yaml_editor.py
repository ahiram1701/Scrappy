"""Lectura y escritura de `sources.yaml` preservando su documentacion.

`config/sources.yaml` esta lleno de comentarios que no son adorno: explican por
que Reddit rota subreddits (el rate limit), por que Bluesky necesita
`window_hours` (sin el, `sort=top` devuelve lo mas votado de siempre), o que
Giphy no expone contadores. Todo eso se descubrio probando contra las APIs
reales y es lo primero que alguien necesita al volver al fichero meses despues.

`yaml.safe_dump` borra los comentarios enteros. Por eso aqui se usa
`ruamel.yaml` en modo round-trip, que conserva comentarios, orden, comillas y
formato.

## Que puede y que no puede hacer este editor

**Solo modifica valores de claves que ya existen.** No crea secciones, no borra
nada y no reordena. Es una limitacion deliberada: crear estructura desde una
interfaz es donde un editor automatico haria mas dano, y el fichero de ejemplo
ya trae todas las secciones.

Si una clave no esta en el fichero, `set_value` la ignora y lo dice. Anadirla
es trabajo manual, con su comentario explicando por que.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from scrappy.config.loader import SourcesConfig
from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)


def _build_yaml() -> YAML:
    """Instancia de ruamel configurada para no tocar nada que no se le pida."""
    yaml = YAML()
    yaml.preserve_quotes = True
    # Sin esto, ruamel reajusta la indentacion de las listas y el diff sale
    # lleno de ruido que no ha pedido nadie.
    yaml.indent(mapping=2, sequence=4, offset=2)
    # El fichero tiene lineas de comentario largas; envolverlas las romperia.
    yaml.width = 4096
    return yaml


class SourcesYamlEditor:
    """Edita `sources.yaml` conservando sus comentarios.

    Uso:

        editor = SourcesYamlEditor(path)
        editor.load()
        editor.set_value(["ranking", "weights", "engagement"], 0.6)
        editor.save()          # valida antes de escribir
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._yaml = _build_yaml()
        self._data: Any = None
        self._dirty = False

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Lee el fichero.

        Raises:
            ConfigError: no existe o no es YAML valido.
        """
        if not self.path.exists():
            raise ConfigError(
                f"{self.path} no existe. Copia config/sources.example.yaml para empezar."
            )
        try:
            self._data = self._yaml.load(self.path.read_text(encoding="utf-8"))
        except YAMLError as exc:
            raise ConfigError(f"{self.path} no es YAML valido: {exc}") from exc

        if self._data is None:
            raise ConfigError(f"{self.path} esta vacio")
        self._dirty = False

    @property
    def loaded(self) -> bool:
        return self._data is not None

    @property
    def dirty(self) -> bool:
        """True si hay cambios sin guardar."""
        return self._dirty

    # ------------------------------------------------------------------
    # Lectura y escritura de valores
    # ------------------------------------------------------------------
    def get_value(self, path: list[str], default: Any = None) -> Any:
        """Valor en una ruta de claves, o `default` si falta algun tramo."""
        node = self._require_data()
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set_value(self, path: list[str], value: Any) -> bool:
        """Cambia el valor de una clave existente.

        Returns:
            True si se aplico. False si la clave no existe, en cuyo caso no se
            toca nada: crear estructura desde la interfaz es justo lo que este
            editor no hace.
        """
        node = self._require_data()
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                log.warning("yaml_path_missing", path=".".join(path))
                return False
            node = node[key]

        leaf = path[-1]
        if not isinstance(node, dict) or leaf not in node:
            log.warning("yaml_key_missing", path=".".join(path))
            return False

        if node[leaf] == value:
            return True  # nada que hacer, pero la ruta era valida

        node[leaf] = value
        self._dirty = True
        return True

    def source_names(self) -> list[str]:
        """Fuentes presentes en el fichero, en su orden original."""
        sources = self.get_value(["sources"], {})
        return list(sources) if isinstance(sources, dict) else []

    # ------------------------------------------------------------------
    # Guardado
    # ------------------------------------------------------------------
    def render(self) -> str:
        """Serializa a texto sin escribir en disco."""
        buffer = io.StringIO()
        self._yaml.dump(self._require_data(), buffer)
        return buffer.getvalue()

    def validate(self) -> SourcesConfig:
        """Comprueba que lo editado sigue cumpliendo el esquema del proyecto.

        Se valida el texto ya serializado, no la estructura en memoria, para
        que sea exactamente lo que se va a escribir lo que se valida.

        Raises:
            ConfigError: la configuracion resultante no es valida.
        """
        import yaml as pyyaml

        try:
            plano = pyyaml.safe_load(self.render())
        except pyyaml.YAMLError as exc:  # pragma: no cover - ruamel ya lo evita
            raise ConfigError(f"lo editado no es YAML valido: {exc}") from exc

        try:
            return SourcesConfig.model_validate(plano)
        except ValueError as exc:
            raise ConfigError(f"la configuracion editada no es valida:\n{exc}") from exc

    def save(self) -> SourcesConfig:
        """Valida y escribe.

        Se valida **antes** de tocar el fichero: si algo esta mal, el usuario se
        queda con lo que tenia en vez de con un `sources.yaml` roto que impida
        arrancar.

        Returns:
            La configuracion ya validada, lista para recargar en la aplicacion.
        """
        config = self.validate()
        self.path.write_text(self.render(), encoding="utf-8")
        self._dirty = False
        log.info("sources_yaml_saved", path=str(self.path))
        return config

    # ------------------------------------------------------------------
    def _require_data(self) -> Any:
        if self._data is None:
            raise ConfigError("hay que llamar a load() antes de usar el editor")
        return self._data
