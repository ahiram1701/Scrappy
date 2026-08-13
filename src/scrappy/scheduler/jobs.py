"""Scheduler del pipeline.

Se usa APScheduler directamente en vez del `JobQueue` de python-telegram-bot
para que el modo "solo scheduler" (`scrappy run --no-bot`) no dependa de tener
un bot escuchando actualizaciones.

Dos decisiones de comportamiento que conviene conocer:

- `max_instances=1` y `coalesce=True`: si un run tarda mas que el intervalo, no
  se lanza otro encima ni se acumulan ejecuciones pendientes. Dos pipelines a la
  vez competirian por el rate limit de Telegram y publicarian duplicados.
- La primera ejecucion se retrasa un minuto desde el arranque, para que el
  proceso termine de levantarse antes de ponerse a descargar.
"""

from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from scrappy.app import ScrappyApp
from scrappy.core.models import utcnow
from scrappy.core.tiempo import formato_local
from scrappy.observability.logging import get_logger

log = get_logger(__name__)

_JOB_ID = "scrappy-pipeline"
_STARTUP_DELAY = timedelta(minutes=1)


class PipelineScheduler:
    """Lanza el pipeline cada `SCRAPPY_SCHEDULE_INTERVAL_MINUTES`."""

    def __init__(self, app: ScrappyApp) -> None:
        self._app = app
        # En la zona del usuario, no en UTC. Con un intervalo en minutos da
        # igual para el disparo, pero no para lo que se lee por pantalla: el
        # panel decia «proxima ronda: 05:00» cuando en tu reloj eran las 23:00.
        self._scheduler = AsyncIOScheduler(timezone=app.settings.tzinfo)

    def start(self) -> None:
        """Programa el job y arranca el scheduler."""
        settings = self._app.settings
        if not settings.schedule_enabled:
            log.info("scheduler_disabled", detail="SCRAPPY_SCHEDULE_ENABLED=false")
            return

        self._scheduler.add_job(
            self._tick,
            trigger=IntervalTrigger(minutes=settings.schedule_interval_minutes),
            id=_JOB_ID,
            name="pipeline",
            next_run_time=utcnow() + _STARTUP_DELAY,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        self._scheduler.start()
        log.info(
            "scheduler_started",
            interval_minutes=settings.schedule_interval_minutes,
            items_per_run=settings.items_per_run,
            # Va en el log a proposito: «publico de madrugada» casi siempre es
            # esto, y sin verlo escrito no hay forma de saberlo.
            timezone=str(settings.tzinfo),
        )

    async def _tick(self) -> None:
        """Una ejecucion programada.

        Nunca propaga excepciones: si lo hiciera, APScheduler desprogramaria el
        job y el bot dejaria de publicar en silencio.
        """
        if self._app.paused:
            log.info("scheduled_run_skipped", reason="pausado con /pause")
            return

        try:
            report = await self._app.run_pipeline()
            log.info("scheduled_run_done", summary=report.summary_line())
        except Exception as exc:  # el scheduler debe sobrevivir a cualquier fallo
            log.exception("scheduled_run_failed", error=str(exc))

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler_stopped")

    @property
    def next_run_at(self) -> str | None:
        """Cuando toca la proxima ejecucion, escrito para leerlo de un vistazo."""
        job = self._scheduler.get_job(_JOB_ID) if self._scheduler.running else None
        if job is None or job.next_run_time is None:
            return None
        return formato_local(job.next_run_time, self._app.settings.tzinfo)
