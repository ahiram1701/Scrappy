"""Workspaces efimeros: la pieza que garantiza que no queda nada en el disco.

La regla del proyecto es que el contenido solo vive en Telegram. Cumplirla no
es cuestion de acordarse de borrar: es cuestion de que borrar sea inevitable.
Por eso todo fichero temporal nace dentro de un `EphemeralWorkspace`, que:

- crea un directorio propio por item, con nombre aleatorio;
- lo borra en su `finally`, ocurra lo que ocurra: retorno normal, excepcion,
  `asyncio.CancelledError` o `KeyboardInterrupt`;
- se registra en un conjunto global para que un `SIGTERM` pueda purgar los
  workspaces vivos antes de que el proceso muera.

Ademas `sweep_orphans()` limpia al arrancar lo que un crash anterior pudiera
haber dejado. Entre las tres cosas, el unico escenario en el que sobrevive un
fichero es un `SIGKILL` (`kill -9`), y de ese ni el sistema operativo avisa.
"""

from __future__ import annotations

import contextlib
import shutil
import signal
import tempfile
import threading
import time
import types
from collections.abc import Iterator
from pathlib import Path

from scrappy.observability.logging import get_logger

log = get_logger(__name__)

_PREFIX = "scrappy-ws-"

# Workspaces vivos ahora mismo. Lo consulta el handler de senales, que puede
# ejecutarse en cualquier momento, de ahi el lock.
_ACTIVE: set[Path] = set()
_ACTIVE_LOCK = threading.Lock()

_HANDLERS_INSTALLED = False


class EphemeralWorkspace:
    """Directorio temporal con borrado garantizado.

    Uso normal, como gestor de contexto:

        with EphemeralWorkspace(root) as ws:
            path = ws.path / "video.mp4"
            ...
        # aqui `ws.path` ya no existe

    Args:
        root: directorio padre. En Docker apunta a un tmpfs, de modo que el
            medio ni siquiera llega a escribirse en el disco fisico. Si es
            None se usa el temporal del sistema.
    """

    __slots__ = ("_path", "_root")

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._path: Path | None = None

    # -- ciclo de vida ---------------------------------------------------
    def __enter__(self) -> EphemeralWorkspace:
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)
        created = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=self._root))
        self._path = created
        with _ACTIVE_LOCK:
            _ACTIVE.add(created)
        log.debug("workspace_opened", path=str(created))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        # Sin `return True`: las excepciones se propagan, solo se limpia.
        self.cleanup()

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("EphemeralWorkspace se usa como gestor de contexto")
        return self._path

    def cleanup(self) -> None:
        """Borra el directorio. Es idempotente y nunca lanza."""
        path = self._path
        if path is None:
            return
        self._path = None
        with _ACTIVE_LOCK:
            _ACTIVE.discard(path)
        _remove_tree(path)
        log.debug("workspace_closed", path=str(path))

    # -- utilidades ------------------------------------------------------
    def file(self, name: str) -> Path:
        """Ruta dentro del workspace. No crea nada."""
        return self.path / name

    def is_empty(self) -> bool:
        return not any(self.path.iterdir())

    def total_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.path.rglob("*") if f.is_file())


def _remove_tree(path: Path) -> None:
    """Borra un arbol de ficheros con insistencia razonable.

    En Windows es habitual que un fichero siga bloqueado unos milisegundos
    despues de que ffmpeg cierre, o que quede marcado como solo lectura. Un
    unico `rmtree` fallaria justo ahi, que es precisamente el caso que no nos
    podemos permitir: el borrado del contenido no es opcional.

    Por eso se reintenta tres veces quitando el flag de solo lectura entre
    intentos, y como ultimo recurso se fuerza con `ignore_errors`.
    """
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            log.debug("cleanup_retry", path=str(path), attempt=attempt + 1, error=str(exc))
            _clear_readonly(path)
            time.sleep(0.1)

    shutil.rmtree(path, ignore_errors=True)
    if path.exists():  # pragma: no cover - solo si el SO nos lo impide del todo
        log.warning(
            "workspace_cleanup_failed",
            path=str(path),
            detail="no se pudo borrar; se reintentara en el proximo arranque",
        )


def _clear_readonly(path: Path) -> None:
    """Quita el flag de solo lectura de todo el arbol, si se puede."""
    for entry in (path, *path.rglob("*")):
        with contextlib.suppress(OSError):
            entry.chmod(0o700)


def purge_active() -> int:
    """Borra todos los workspaces vivos. La usa el handler de senales."""
    with _ACTIVE_LOCK:
        pending = list(_ACTIVE)
        _ACTIVE.clear()
    for path in pending:
        _remove_tree(path)
    if pending:
        log.info("workspaces_purged", count=len(pending))
    return len(pending)


def sweep_orphans(root: Path | None = None) -> int:
    """Borra workspaces de ejecuciones anteriores que quedaran huerfanos.

    Se llama al arrancar. Sin esto, un `kill -9` dejaria un video en el disco
    hasta el siguiente reinicio del sistema.
    """
    base = root or Path(tempfile.gettempdir())
    if not base.exists():
        return 0

    removed = 0
    with _ACTIVE_LOCK:
        active = set(_ACTIVE)

    for entry in base.glob(f"{_PREFIX}*"):
        if entry in active or not entry.is_dir():
            continue
        _remove_tree(entry)
        removed += 1

    if removed:
        log.info("orphan_workspaces_removed", count=removed, root=str(base))
    return removed


def iter_active() -> Iterator[Path]:
    """Workspaces vivos. Para `/health` y para los tests."""
    with _ACTIVE_LOCK:
        yield from tuple(_ACTIVE)


def install_signal_handlers() -> None:
    """Purga los workspaces ante SIGINT/SIGTERM antes de que el proceso muera.

    Es idempotente y no rompe en plataformas donde una senal no existe
    (SIGTERM no esta disponible en todos los entornos Windows).
    """
    global _HANDLERS_INSTALLED
    if _HANDLERS_INSTALLED:
        return

    def _handler(signum: int, _frame: types.FrameType | None) -> None:
        purge_active()
        # Se restaura el comportamiento por defecto y se reenvia la senal, para
        # no alterar el codigo de salida ni impedir el apagado.
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _handler)

    _HANDLERS_INSTALLED = True
