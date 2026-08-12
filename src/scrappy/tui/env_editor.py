"""Lectura y escritura del `.env` preservando sus comentarios.

Mismo problema que con `sources.yaml` y misma solucion: el `.env.example`
documenta cada variable —de donde sale el token, por que el chat id de un
privado va positivo, que Reddit ya no pide credenciales— y reescribirlo con un
volcado plano borraria todo eso.

No hay un ruamel para dotenv, pero tampoco hace falta: el formato es una linea
por variable, asi que basta con recorrerlas conservando lo que no se toca.

## Aqui hay secretos

- Los valores **nunca** se registran en el log, ni siquiera en DEBUG. El
  redactor de `logging.py` cubre los diccionarios que se le pasan, pero la
  disciplina de no intentarlo siquiera es mas barata que confiar en el.
- `is_secret()` marca que campos debe enmascarar la interfaz.
- Al guardar se valida con `Settings`: un `.env` invalido impide arrancar el
  proyecto entero, asi que si algo esta mal es mejor quedarse con el anterior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scrappy.config.settings import Settings
from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

#: Fragmentos que, si aparecen en el nombre de una variable, la marcan como
#: secreta. Se compara en minusculas.
_SECRETOS = ("token", "secret", "key", "password", "cookie")


@dataclass(slots=True)
class _Linea:
    """Una linea del fichero.

    Si `key` es None es un comentario o una linea en blanco, y se conserva tal
    cual sin interpretarla.
    """

    raw: str
    key: str | None = None
    value: str = ""


def is_secret(key: str) -> bool:
    """True si el valor de esa variable no debe mostrarse en claro."""
    minuscula = key.lower()
    return any(fragmento in minuscula for fragmento in _SECRETOS)


class EnvEditor:
    """Edita un fichero `.env` sin perder sus comentarios.

    Uso:

        editor = EnvEditor(Path(".env"))
        editor.load()
        editor.set_value("SCRAPPY_ITEMS_PER_RUN", "8")
        editor.save()      # valida antes de escribir
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lineas: list[_Linea] = []
        self._cargado = False
        self._dirty = False

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Lee el fichero.

        Raises:
            ConfigError: no existe.
        """
        if not self.path.exists():
            raise ConfigError(
                f"{self.path} no existe. Ejecuta `scrappy init` para crearlo, o "
                "copia .env.example a .env"
            )

        self._lineas = [
            self._parsear(linea) for linea in self.path.read_text(encoding="utf-8").splitlines()
        ]
        self._cargado = True
        self._dirty = False

    @staticmethod
    def _parsear(raw: str) -> _Linea:
        limpia = raw.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            return _Linea(raw=raw)

        key, _, value = limpia.partition("=")
        key = key.strip()
        if not key.replace("_", "").isalnum():
            # Algo que parece una asignacion pero no lo es; se deja intacto.
            return _Linea(raw=raw)
        return _Linea(raw=raw, key=key, value=value.strip())

    @property
    def dirty(self) -> bool:
        return self._dirty

    # ------------------------------------------------------------------
    # Lectura y escritura
    # ------------------------------------------------------------------
    def keys(self) -> list[str]:
        """Variables presentes, en el orden del fichero."""
        return [linea.key for linea in self._require() if linea.key]

    def get_value(self, key: str, default: str = "") -> str:
        for linea in self._require():
            if linea.key == key:
                return linea.value
        return default

    def set_value(self, key: str, value: str) -> None:
        """Cambia el valor de una variable, o la anade si no estaba.

        A diferencia del editor de YAML, aqui si se permite crear: el `.env`
        es una lista plana de variables sin estructura que romper, y una
        variable que falte es un caso normal cuando se anade una opcion nueva.
        """
        value = value.strip()

        for linea in self._require():
            if linea.key == key:
                if linea.value == value:
                    return
                linea.value = value
                linea.raw = f"{key}={value}"
                self._dirty = True
                # A proposito no se registra el valor: puede ser un secreto.
                log.debug("env_value_set", key=key)
                return

        self._lineas.append(_Linea(raw=f"{key}={value}", key=key, value=value))
        self._dirty = True
        log.debug("env_value_added", key=key)

    # ------------------------------------------------------------------
    # Guardado
    # ------------------------------------------------------------------
    def render(self) -> str:
        texto = "\n".join(linea.raw for linea in self._require())
        return texto + "\n" if texto and not texto.endswith("\n") else texto

    def validate(self) -> Settings:
        """Comprueba que lo editado produce unos ajustes validos.

        Raises:
            ConfigError: falta algo obligatorio o un valor no encaja.
        """
        valores = {linea.key: linea.value for linea in self._require() if linea.key}
        # `Settings` lee del entorno por defecto; aqui se le pasan los valores
        # editados directamente para validar el fichero y no el entorno actual.
        sin_prefijo = {
            key.removeprefix("SCRAPPY_").lower(): value
            for key, value in valores.items()
            if key.startswith("SCRAPPY_")
        }
        try:
            return Settings(_env_file=None, **sin_prefijo)  # type: ignore[arg-type]
        except ValueError as exc:
            raise ConfigError(f"la configuracion editada no es valida:\n{exc}") from exc

    def save(self) -> Settings:
        """Valida y escribe.

        Se valida antes de tocar el fichero: un `.env` roto impide arrancar el
        proyecto, asi que ante la duda es mejor conservar el que habia.
        """
        settings = self.validate()
        self.path.write_text(self.render(), encoding="utf-8")
        self._dirty = False
        # Se registra la ruta, nunca el contenido.
        log.info("env_saved", path=str(self.path))
        return settings

    # ------------------------------------------------------------------
    def _require(self) -> list[_Linea]:
        if not self._cargado:
            raise ConfigError("hay que llamar a load() antes de usar el editor")
        return self._lineas
