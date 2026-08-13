"""Regenera las capturas de la TUI que ilustran la documentacion.

Se ejecuta a mano cuando cambia una pantalla:

    python scripts/capturas.py

Usa una configuracion de mentira -token falso, estado en memoria, catalogo de
ejemplo- por dos motivos: que las capturas no lleven datos de nadie, y que
salgan iguales en cualquier maquina.

No sale a la red, y eso obliga a dejar el token vacio: con uno de mentira,
python-telegram-bot lo valida contra la API al arrancar y la captura sale con
un error en vez de con la pantalla. Asi que las capturas muestran el modo solo
lectura, que es tambien lo primero que ve quien acaba de instalarlo.

La pantalla de candidatos se captura sin explorar, que es como se ve al abrirla.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from scrappy.tui.main import ScrappyTUI

DESTINO = Path("docs/assets")

#: Ancho y alto en celdas. El alto tiene que dar para el panel entero: sus
#: secciones estan en un VerticalScroll, asi que un alto corto no falla, solo
#: deja fuera de la captura lo que hay mas abajo.
TAMANO = (110, 48)

ENV_DEMO = """\
SCRAPPY_TELEGRAM_BOT_TOKEN=
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=-1001234567890
SCRAPPY_TELEGRAM_ADMIN_IDS=123456789
SCRAPPY_STATE_BACKEND=memory
SCRAPPY_TIMEZONE=America/Mexico_City
"""

PANTALLAS = {
    "tui-panel": "d",
    "tui-candidatos": "c",
    "tui-configuracion": "s",
}


async def capturar(directorio: Path) -> None:
    env_path = directorio / ".env"
    yaml_path = directorio / "sources.yaml"
    yaml_path.write_text(
        Path("config/sources.example.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    env_path.write_text(
        f"{ENV_DEMO}SCRAPPY_SOURCES_CONFIG_PATH={yaml_path.as_posix()}\n", encoding="utf-8"
    )

    tui = ScrappyTUI(env_path=env_path, show_wizard=False)
    async with tui.run_test(size=TAMANO) as pilot:
        for nombre, tecla in PANTALLAS.items():
            await pilot.press(tecla)
            await pilot.pause()

            destino = DESTINO / f"{nombre}.svg"
            destino.write_text(pilot.app.export_screenshot(title="Scrappy"), encoding="utf-8")
            print(f"escrita {destino}")


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(capturar(Path(tmp)))


if __name__ == "__main__":
    main()
