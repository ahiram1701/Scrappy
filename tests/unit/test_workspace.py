"""Pruebas del `EphemeralWorkspace`.

Es la pieza que sostiene la promesa central del proyecto —el contenido no se
queda en la maquina— asi que se prueba mas a fondo que ninguna otra: el camino
feliz, la excepcion, la cancelacion, el ficheros bloqueado y los huerfanos.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scrappy.download.workspace import (
    EphemeralWorkspace,
    iter_active,
    purge_active,
    sweep_orphans,
)


def test_crea_y_borra_el_directorio(tmp_path: Path) -> None:
    with EphemeralWorkspace(tmp_path) as workspace:
        path = workspace.path
        assert path.exists()
        (path / "video.mp4").write_bytes(b"x" * 1024)

    assert not path.exists()


def test_borra_aunque_haya_excepcion(tmp_path: Path) -> None:
    captured: Path | None = None

    with (
        pytest.raises(RuntimeError, match="algo fallo"),
        EphemeralWorkspace(tmp_path) as workspace,
    ):
        captured = workspace.path
        (captured / "video.mp4").write_bytes(b"x" * 1024)
        raise RuntimeError("algo fallo")

    assert captured is not None
    assert not captured.exists()


async def test_borra_al_cancelar(tmp_path: Path) -> None:
    """Una tarea cancelada a mitad de descarga no puede dejar el fichero."""
    captured: dict[str, Path] = {}
    started = asyncio.Event()

    async def descarga_larga() -> None:
        with EphemeralWorkspace(tmp_path) as workspace:
            captured["path"] = workspace.path
            (workspace.path / "video.mp4").write_bytes(b"x" * 4096)
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(descarga_larga())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not captured["path"].exists()


def test_se_registra_mientras_vive(tmp_path: Path) -> None:
    with EphemeralWorkspace(tmp_path) as workspace:
        assert workspace.path in set(iter_active())
    # Se comprueba el estado interno a proposito: es la garantia del proyecto.
    assert workspace._path is None


def test_purge_active_borra_los_vivos(tmp_path: Path) -> None:
    workspace = EphemeralWorkspace(tmp_path)
    workspace.__enter__()
    path = workspace.path
    (path / "meme.gif").write_bytes(b"gif")

    assert purge_active() >= 1
    assert not path.exists()


def test_sweep_orphans_limpia_restos_de_un_crash(tmp_path: Path) -> None:
    """Simula lo que quedaria tras un `kill -9` a mitad de descarga."""
    huerfano = tmp_path / "scrappy-ws-abandonado"
    huerfano.mkdir()
    (huerfano / "video.mp4").write_bytes(b"x" * 2048)

    ajeno = tmp_path / "otra-cosa"
    ajeno.mkdir()
    (ajeno / "importante.txt").write_text("no tocar")

    removed = sweep_orphans(tmp_path)

    assert removed == 1
    assert not huerfano.exists()
    # Solo se borra lo que lleva nuestro prefijo: nada mas del temporal.
    assert ajeno.exists()


def test_borra_ficheros_de_solo_lectura(tmp_path: Path) -> None:
    """En Windows un fichero recien cerrado puede quedar marcado solo-lectura."""
    with EphemeralWorkspace(tmp_path) as workspace:
        path = workspace.path
        bloqueado = path / "video.mp4"
        bloqueado.write_bytes(b"x" * 512)
        bloqueado.chmod(0o444)

    assert not path.exists()


def test_utilidades_de_inspeccion(tmp_path: Path) -> None:
    with EphemeralWorkspace(tmp_path) as workspace:
        assert workspace.is_empty()
        workspace.file("a.bin").write_bytes(b"x" * 100)
        assert not workspace.is_empty()
        assert workspace.total_bytes() == 100


def test_usar_fuera_del_contexto_es_un_error() -> None:
    workspace = EphemeralWorkspace()
    with pytest.raises(RuntimeError, match="gestor de contexto"):
        _ = workspace.path


def test_cleanup_es_idempotente(tmp_path: Path) -> None:
    workspace = EphemeralWorkspace(tmp_path)
    workspace.__enter__()
    workspace.cleanup()
    workspace.cleanup()  # no debe lanzar
