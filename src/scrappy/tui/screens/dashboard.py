"""Panel: estado del sistema, fuentes, estadisticas y control del scheduler.

Todo lo que muestra sale de `ScrappyApp.health()` y `source_statuses()`, que ya
alimentan `scrappy health` y el comando `/sources` del bot. Aqui solo se pintan.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from scrappy.core.models import utcnow
from scrappy.observability.logging import get_logger
from scrappy.tui.widgets.confirm import ConfirmModal

if TYPE_CHECKING:
    from scrappy.tui.main import ScrappyTUI

log = get_logger(__name__)


class DashboardScreen(Screen[None]):
    """Vista de estado general."""

    @property
    def tui(self) -> ScrappyTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll():
            with Vertical(classes="seccion"):
                yield Static("Sistema", classes="seccion-titulo")
                yield Static(id="salud")
            # El scheduler va antes que las fuentes a proposito: «cuando
            # publica?» se consulta a diario y la tabla de nueve fuentes es
            # material de referencia. Con la tabla delante, en una terminal de
            # 24 filas el scheduler caia por debajo del pliegue.
            with Vertical(classes="seccion"):
                yield Static("Scheduler", classes="seccion-titulo")
                yield Static(id="estado-scheduler")
                with Horizontal(id="acciones-scheduler"):
                    yield Button("Arrancar", id="arrancar", variant="success")
                    yield Button("Pausar", id="pausar", variant="warning")
                    yield Button("Reanudar", id="reanudar")
            with Vertical(classes="seccion"):
                yield Static("Fuentes", classes="seccion-titulo")
                yield DataTable(id="tabla-fuentes", cursor_type="row")
            with Vertical(classes="seccion"):
                yield Static("Publicado", classes="seccion-titulo")
                yield Static(id="stats")
            with Vertical(classes="seccion"):
                yield Static("Arranque automatico", classes="seccion-titulo")
                yield Static(id="estado-autoarranque")
                with Horizontal(id="acciones-autoarranque"):
                    yield Button("Al iniciar sesion", id="autoarranque-on", variant="success")
                    yield Button("Sin iniciar sesion", id="autoarranque-sistema", variant="primary")
                    yield Button("Desactivar", id="autoarranque-off")
            with Vertical(classes="seccion"):
                yield Static("Configuracion", classes="seccion-titulo")
                yield Static(id="estado-config")
                with Horizontal(id="acciones-config"):
                    yield Button("Recargar configuracion", id="recargar-config")
                    yield Button("Renovar cookies de X", id="renovar-cookies")
        yield Footer()

    async def on_mount(self) -> None:
        tabla = self.query_one("#tabla-fuentes", DataTable)
        tabla.add_columns("fuente", "activa", "configurada", "detalle")
        await self.refresh_data()

    # ------------------------------------------------------------------
    async def refresh_data(self) -> None:
        """Recarga salud, fuentes y estadisticas."""
        scrappy = self.tui.scrappy
        if scrappy is None:
            self.query_one("#salud", Static).update(
                "Scrappy no ha arrancado. Revisa la barra de estado."
            )
            return

        report = await scrappy.health()
        self.query_one("#salud", Static).update(
            "\n".join(
                [
                    f"ffmpeg          {'OK' if report.ffmpeg else 'NO ENCONTRADO'}",
                    f"estado          {report.state_backend} "
                    f"({report.state_items} items recordados)",
                    f"telegram        "
                    f"{'configurado' if report.telegram_configured else 'SIN configurar'}",
                    f"workspaces      {report.active_workspaces} "
                    f"({'limpio' if report.active_workspaces == 0 else 'descargas en curso'})",
                ]
            )
        )

        tabla = self.query_one("#tabla-fuentes", DataTable)
        tabla.clear()
        for estado in report.sources:
            tabla.add_row(
                estado.name,
                "si" if estado.enabled else "no",
                "si" if estado.configured else "no",
                estado.detail or "—",
            )

        await self._refresh_stats(scrappy)
        self._refresh_scheduler()
        self._refresh_autoarranque()
        self._refresh_config(scrappy)

    def sincronizar(self) -> None:
        """Tras recargar. Aqui todo es local, asi que se refresca entero.

        Encolado, no en paralelo: mientras corre el manejador que pidio la
        recarga, los widgets estan a mitad de cambiar.
        """
        self.app.call_later(self.refresh_data)

    def _refresh_autoarranque(self) -> None:
        estado = self.tui.autoarranque.status()
        lineas = [estado.detalle]

        # El aviso importa: con la tarea de fondo activa hay dos procesos que
        # pueden publicar. No salen duplicados -la deduplicacion lo impide-
        # pero conviene saber quien esta publicando.
        if estado.activo and self.tui.scheduler is not None and self.tui.scheduler.next_run_at:
            lineas.append(
                "Ojo: tambien tienes el scheduler de esta ventana en marcha. "
                "Publican los dos; no saldra nada repetido, pero son dos."
            )

        self.query_one("#estado-autoarranque", Static).update("\n".join(lineas))

        activar = self.query_one("#autoarranque-on", Button)
        sin_sesion = self.query_one("#autoarranque-sistema", Button)
        desactivar = self.query_one("#autoarranque-off", Button)

        activar.disabled = not estado.disponible or estado.activo
        # «Sin iniciar sesion» sigue disponible con el de sesion puesto: es un
        # ascenso, no un duplicado, y sustituye la misma tarea.
        sin_sesion.disabled = not estado.disponible or estado.modo == "sistema"
        desactivar.disabled = not estado.disponible or not estado.activo

    def _refresh_config(self, scrappy: object) -> None:
        settings = scrappy.settings  # type: ignore[attr-defined]
        self.query_one("#estado-config", Static).update(
            "\n".join(
                [
                    f"fichero         {self.tui.env_path}",
                    # La zona resuelta, no la escrita: si el `.env` la lleva
                    # vacia lo util es saber cual se detecto.
                    f"zona horaria    {settings.tzinfo}",
                    f"catalogo        {settings.sources_config_path}",
                ]
            )
        )

    async def _refresh_stats(self, scrappy: object) -> None:
        state = scrappy.state  # type: ignore[attr-defined]
        semana = await state.stats(since=utcnow() - timedelta(days=7))
        total = await state.total_published()

        if semana:
            detalle = "  ".join(f"{fuente}: {n}" for fuente, n in sorted(semana.items()))
        else:
            detalle = "nada en los ultimos 7 dias"
        self.query_one("#stats", Static).update(f"{detalle}\nhistorico: {total}")

    def _refresh_scheduler(self) -> None:
        """Estado del scheduler, distinguiendo por que no esta publicando.

        Antes, los tres motivos distintos por los que no habia rondas -sin
        arrancar, desactivado en la configuracion, o sin Telegram- se pintaban
        todos como «parado». Con `SCRAPPY_SCHEDULE_ENABLED=true` guardado eso
        se lee como una contradiccion, porque lo es: la palabra «parado» tapaba
        cual de los tres era.
        """
        scheduler = self.tui.scheduler
        scrappy = self.tui.scrappy
        if scheduler is None or scrappy is None:
            return

        cada = scrappy.settings.schedule_interval_minutes
        cuantos = scrappy.settings.items_per_run

        if not scheduler.enabled:
            texto = (
                "desactivado en la configuracion (Programacion → Scheduler activo).\n"
                "Solo publicara cuando se lo pidas, con «Publicar» o con /fetch."
            )
        elif not self.tui.can_publish:
            texto = (
                "no puede arrancar: falta configurar Telegram.\n"
                "Sin eso, cada ronda seria un fallo y ninguna publicacion."
            )
        elif not scheduler.running:
            texto = "activado en la configuracion, pero sin arrancar. Pulsa «Arrancar»."
        elif scrappy.paused:
            texto = (
                "en pausa. Las rondas automaticas estan detenidas; «Publicar» "
                "y /fetch siguen funcionando."
            )
        else:
            texto = (
                f"en marcha: {cuantos} items cada {cada} min\n"
                f"proxima ronda: {scheduler.next_run_at}"
            )

        self.query_one("#estado-scheduler", Static).update(texto)

        # Un boton que no puede hacer nada tiene que verse deshabilitado, no
        # responder con silencio. «Arrancar» con el scheduler desactivado en la
        # configuracion no hacia absolutamente nada, sin decirlo.
        en_marcha = scheduler.running
        self.query_one("#arrancar", Button).disabled = (
            en_marcha or not scheduler.enabled or not self.tui.can_publish
        )
        self.query_one("#pausar", Button).disabled = not en_marcha or scrappy.paused
        self.query_one("#reanudar", Button).disabled = not scrappy.paused

    # ------------------------------------------------------------------
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Antes de exigir que Scrappy este montado: recargar es justamente lo
        # que puede arreglar un arranque fallido por configuracion.
        if event.button.id == "recargar-config":
            await self.tui.action_recargar()
            await self.refresh_data()
            return

        if event.button.id == "renovar-cookies":
            await self._renovar_cookies()
            return

        if event.button.id in {"autoarranque-on", "autoarranque-sistema", "autoarranque-off"}:
            await self._cambiar_autoarranque(boton=event.button.id)
            return

        scrappy = self.tui.require_scrappy()
        scheduler = self.tui.scheduler
        if scrappy is None or scheduler is None:
            return

        match event.button.id:
            case "arrancar":
                # Los botones ya salen deshabilitados cuando no pueden hacer
                # nada; esto cubre el caso de pulsar antes de que se repinten.
                if not self.tui.can_publish:
                    self.notify(
                        "Sin Telegram configurado el scheduler no puede publicar.",
                        severity="warning",
                    )
                    return
                if scheduler.start():
                    self.tui.set_status("Scheduler arrancado")
                else:
                    self.notify(
                        "El scheduler esta desactivado en la configuracion. "
                        "Activalo en Configuracion → Programacion y recarga.",
                        severity="warning",
                        timeout=10,
                    )
            case "pausar":
                scrappy.paused = True
                self.tui.set_status("Scheduler en pausa")
            case "reanudar":
                scrappy.paused = False
                self.tui.set_status("Scheduler reanudado")

        self._refresh_scheduler()
        self._refresh_autoarranque()

    async def _renovar_cookies(self) -> None:
        """Reextrae la sesion de X del navegador, sin salir de la TUI.

        Es la unica credencial del proyecto que caduca sola, y hasta ahora
        arreglarla obligaba a recordar una invocacion de yt-dlp con la ruta de
        un perfil que no se llama como uno cree. La logica esta en
        `sources.x_cookies`, compartida con `scrappy cookies` y con el boton de
        Telegram: las tres dicen exactamente lo mismo.
        """
        from scrappy.config.settings import XBackend
        from scrappy.sources.x_cookies import renovar_cookies

        scrappy = self.tui.scrappy
        if scrappy is None:
            self.notify("Scrappy no esta montado.", severity="warning")
            return
        if scrappy.settings.x_backend is not XBackend.SCRAPE:
            # El boton esta a la vista siempre; explicar por que no aplica es
            # mejor que esconderlo y que nadie sepa que existe.
            self.notify("Solo hace falta con SCRAPPY_X_BACKEND=scrape.", severity="information")
            return

        # Bloquea unos segundos leyendo una base de datos: fuera del bucle.
        resultado = await asyncio.to_thread(renovar_cookies, scrappy.settings)
        self.notify(
            resultado.detalle,
            severity="information" if resultado.ok else "error",
            timeout=10,
        )

    async def _cambiar_autoarranque(self, *, boton: str) -> None:
        """Registra o quita el arranque automatico, confirmando antes.

        Se confirma porque esto toca el Programador de tareas de Windows, que
        esta fuera de este proyecto: quien lo pulse tiene que saber que se le
        queda algo instalado y como quitarlo.
        """
        if boton == "autoarranque-sistema":
            titulo, texto_boton = "Arrancar sin iniciar sesion", "Activar"
            detalle = (
                "Scrappy arrancara al encender el equipo, aunque no entre nadie. Es "
                "la unica forma de que publique con el equipo encendido y la sesion "
                "cerrada.\n\n"
                "Windows te va a pedir permiso de administrador (UAC): una tarea que "
                "corre sin sesion solo se puede crear elevada. Se registra con tu "
                "propia cuenta y sin guardar tu contrasena.\n\n"
                "Para quitarlo: «Desactivar», que volvera a pedirtelo."
            )
        elif boton == "autoarranque-on":
            titulo, texto_boton = "Arrancar Scrappy al iniciar sesion", "Activar"
            detalle = (
                "Al iniciar sesion arrancara el bot y el scheduler en segundo plano, "
                "sin ventana. Mientras nadie entre al equipo, no publicara.\n\n"
                "Se intentara con una tarea llamada «Scrappy» en el Programador de "
                "tareas de Windows. Sin elevacion no se puede crear -y esta ventana no "
                "la tiene- asi que lo normal es que se ponga un acceso directo "
                "«Scrappy» en tu carpeta de Inicio, que arranca lo mismo.\n\n"
                "Para quitarlo: este mismo boton."
            )
        else:
            titulo, texto_boton = "Dejar de arrancar solo", "Desactivar"
            detalle = (
                "Se quitaran los dos: la tarea «Scrappy» del Programador de tareas y "
                "el acceso directo «Scrappy» de tu carpeta de Inicio.\n\n"
                "Si estaba puesto «sin iniciar sesion», Windows pedira permiso de "
                "administrador para quitar la tarea.\n\n"
                "Scrappy volvera a publicar solo mientras lo tengas abierto. Si ahora "
                "mismo hay uno corriendo de fondo, seguira hasta que cierres la sesion."
            )

        if not await self.app.push_screen_wait(ConfirmModal(titulo, detalle, texto_boton)):
            return

        autoarranque = self.tui.autoarranque
        match boton:
            case "autoarranque-sistema":
                estado = autoarranque.enable("sistema")
            case "autoarranque-on":
                estado = autoarranque.enable()
            case _:
                estado = autoarranque.disable()

        self.tui.set_status(estado.detalle)
        # Si se pidio activar y no quedo activo, el detalle lleva el motivo.
        if boton != "autoarranque-off" and not estado.activo:
            self.notify(estado.detalle, severity="error", timeout=15)
        self._refresh_autoarranque()
