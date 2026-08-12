"""El pipeline: descubrir, filtrar, rankear, descargar, deduplicar, publicar.

La estructura importante de este fichero es que las cuatro ultimas etapas
ocurren **por item, no por lote**. Podria parecer mas eficiente descargarlo todo
y publicarlo despues, pero eso significaria tener cinco videos en el disco a la
vez. Asi, en cualquier instante hay como mucho un medio materializado, y su
workspace se cierra antes de empezar con el siguiente.

    for candidato in seleccionados:
        with EphemeralWorkspace() as ws:   # <- el borrado esta aqui
            descargar -> hashear -> deduplicar -> publicar
        # el medio ya no existe

Cualquier salida del bloque —retorno, excepcion, cancelacion— pasa por el
`__exit__` del workspace. No hay ninguna ruta que se salte el borrado.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from scrappy.config.loader import SourcesConfig
from scrappy.config.settings import Settings
from scrappy.core.errors import (
    DuplicateItemError,
    ItemError,
    RateLimitedError,
    SourceError,
)
from scrappy.core.models import (
    RawCandidate,
    RunOutcome,
    RunReport,
    ScoredCandidate,
    utcnow,
)
from scrappy.delivery.publisher import TelegramPublisher
from scrappy.download.downloader import Downloader
from scrappy.download.workspace import EphemeralWorkspace
from scrappy.observability.logging import get_logger
from scrappy.ranking.filters import CandidateFilter
from scrappy.ranking.scorer import Scorer
from scrappy.sources.base import SourceAdapter
from scrappy.storage.backends import StateBackendProtocol
from scrappy.storage.dedup import Deduplicator

log = get_logger(__name__)


@dataclass(slots=True)
class DryRunRow:
    """Una fila de la tabla que imprime `--dry-run`."""

    candidate: RawCandidate
    score: float
    verdict: str


@dataclass(slots=True, frozen=True)
class ProgressEvent:
    """Aviso de que el pipeline ha avanzado.

    Existe para la TUI: un run tarda del orden de 15 segundos, y sin avisos
    intermedios la interfaz se queda congelada sin poder decir en que va.

    `stage` es el identificador estable (`discovering`, `filtered`,
    `downloading`, `published`…) y `detail` el texto ya listo para mostrar.
    `current`/`total` solo vienen rellenos en las etapas que se cuentan por
    item; en el resto son None y quien escuche debe tratarlo como
    indeterminado.
    """

    stage: str
    detail: str
    current: int | None = None
    total: int | None = None


#: Se invoca desde el mismo bucle de eventos que el pipeline, asi que puede
#: tocar la interfaz sin preocuparse por hilos. No debe bloquear ni lanzar.
ProgressCallback = Callable[[ProgressEvent], None]


class Pipeline:
    """Ejecuta una ronda completa de curacion."""

    def __init__(
        self,
        *,
        settings: Settings,
        sources_config: SourcesConfig,
        adapters: list[SourceAdapter],
        downloader: Downloader,
        deduplicator: Deduplicator,
        state: StateBackendProtocol,
        publisher: TelegramPublisher | None,
    ) -> None:
        self._settings = settings
        self._sources_config = sources_config
        self._adapters = adapters
        self._downloader = downloader
        self._dedup = deduplicator
        self._state = state
        self._publisher = publisher

        self._filter = CandidateFilter(settings, sources_config.filters)
        self._scorer = Scorer(settings, sources_config)

        self.last_dry_run: list[DryRunRow] = []

    # ------------------------------------------------------------------
    # Ejecucion
    # ------------------------------------------------------------------
    async def run(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> RunReport:
        """Ejecuta una ronda.

        Args:
            on_progress: si se indica, se llama en cada cambio de etapa. Lo usa
                la TUI para no quedarse congelada los ~15 segundos que dura un
                run. La CLI y el bot lo omiten y se comportan igual que antes.
            limit: cuantos items publicar como maximo. Por defecto,
                `SCRAPPY_ITEMS_PER_RUN`.
            dry_run: recorre descubrimiento, filtrado y ranking sin descargar ni
                publicar nada. Es la forma de calibrar los pesos sin ensuciar el
                canal.
        """
        report = RunReport(dry_run=dry_run)
        target = limit if limit is not None else self._settings.items_per_run
        notify = _make_notifier(on_progress)

        notify("discovering", f"Consultando {len(self._adapters)} fuentes…")
        candidates = await self._discover(report)
        report.discovered = len(candidates)
        if not candidates:
            report.finished_at = utcnow()
            notify("finished", "Ninguna fuente devolvio candidatos")
            log.info("run_finished", summary=report.summary_line())
            return report

        notify("filtering", f"{len(candidates)} candidatos, aplicando filtros…")
        accepted, rejected = self._filter.partition(candidates)
        for _ in rejected:
            report.record(RunOutcome.FILTERED)
        log.info("filtered", accepted=len(accepted), rejected=len(rejected))

        # Puerta 1 de dedup: descartar lo ya publicado antes de gastar nada.
        fresh = await self._drop_known(accepted, report)

        # Se piden mas candidatos que items a publicar porque algunos se caeran
        # al descargar (privados, borrados, demasiado grandes).
        # Los votos en contra se releen en cada ronda, no al arrancar: el bot
        # puede llevar dias en marcha y el boton 👎 debe notarse en la
        # siguiente publicacion, no en el siguiente reinicio.
        scorer = Scorer(
            self._settings,
            self._sources_config,
            disliked_authors=await self._state.disliked_authors(),
        )
        notify("ranking", f"Puntuando {len(fresh)} candidatos…")
        selected, low_score = scorer.select(fresh, limit=target * 3)
        for _ in low_score:
            report.record(RunOutcome.LOW_SCORE)

        if dry_run:
            self.last_dry_run = _build_dry_run_rows(selected, rejected, low_score)
            report.finished_at = utcnow()
            notify("finished", report.summary_line())
            log.info("dry_run_finished", summary=report.summary_line())
            return report

        await self._process(selected, target, report, notify)

        report.finished_at = utcnow()
        notify("finished", report.summary_line())
        log.info("run_finished", summary=report.summary_line())
        return report

    # ------------------------------------------------------------------
    # Etapas
    # ------------------------------------------------------------------
    async def _discover(self, report: RunReport) -> list[RawCandidate]:
        """Consulta todas las fuentes en paralelo.

        Que una fuente falle no puede impedir que las demas publiquen, asi que
        se recogen las excepciones en vez de propagarlas.
        """
        if not self._adapters:
            report.errors.append("no hay ninguna fuente utilizable")
            return []

        async def _one(adapter: SourceAdapter) -> list[RawCandidate]:
            budget = self._sources_config.for_source(adapter.name).budget
            return await adapter.discover(budget)

        results = await asyncio.gather(
            *(_one(adapter) for adapter in self._adapters), return_exceptions=True
        )

        candidates: list[RawCandidate] = []
        for adapter, result in zip(self._adapters, results, strict=True):
            if isinstance(result, RateLimitedError):
                message = f"{adapter.name}: rate limit, se omite esta ronda"
                log.warning("source_rate_limited", source=adapter.name)
                report.errors.append(message)
            elif isinstance(result, SourceError):
                log.warning("source_failed", source=adapter.name, error=str(result))
                report.errors.append(str(result))
            elif isinstance(result, BaseException):
                log.exception("source_crashed", source=adapter.name, error=str(result))
                report.errors.append(f"{adapter.name}: error inesperado ({result})")
            else:
                candidates.extend(result)

        return candidates

    async def _drop_known(
        self, candidates: list[RawCandidate], report: RunReport
    ) -> list[RawCandidate]:
        fresh: list[RawCandidate] = []
        for candidate in candidates:
            if await self._dedup.is_known_uid(candidate.uid):
                report.record(RunOutcome.DUPLICATE)
                continue
            fresh.append(candidate)
        return fresh

    async def _process(
        self,
        selected: list[ScoredCandidate],
        target: int,
        report: RunReport,
        notify: _Notifier,
    ) -> None:
        """Descarga y publica hasta `target` items, uno a uno."""
        if self._publisher is None:
            report.errors.append("no hay publisher configurado; nada que publicar")
            return

        published = 0
        for scored in selected:
            if published >= target:
                break
            notify(
                "publishing",
                f"«{scored.candidate.title[:48] or scored.candidate.uid}»",
                current=published + 1,
                total=target,
            )
            if await self._handle_one(scored, report):
                published += 1

    async def _handle_one(self, scored: ScoredCandidate, report: RunReport) -> bool:
        """Ciclo completo de un item. Devuelve True si se publico.

        Todo el trabajo con ficheros ocurre dentro del `with`: al salir, por la
        razon que sea, el medio ya no existe en la maquina.
        """
        assert self._publisher is not None
        candidate = scored.candidate
        bound = log.bind(uid=candidate.uid, score=round(scored.score, 3))

        with EphemeralWorkspace(self._settings.workspace_root) as workspace:
            try:
                media = await self._downloader.fetch(candidate, workspace.path)
                media = await self._dedup.compute_hashes(media)
                await self._dedup.assert_not_duplicate(media)
                item = await self._publisher.publish(scored, media)

            except DuplicateItemError as exc:
                bound.info("duplicate", reason=exc.reason)
                report.record(RunOutcome.DUPLICATE)
                return False

            except ItemError as exc:
                # Un item roto es lo normal, no una emergencia: se anota y se
                # sigue con el siguiente.
                bound.warning("item_failed", error=str(exc))
                report.errors.append(f"{candidate.uid}: {exc}")
                report.record(
                    RunOutcome.PUBLISH_FAILED
                    if "telegram" in str(exc).lower()
                    else RunOutcome.DOWNLOAD_FAILED
                )
                return False

            except asyncio.CancelledError:
                # Se deja subir para que el apagado sea limpio, pero el
                # `__exit__` del workspace ya habra borrado el medio.
                bound.info("item_cancelled")
                raise

        # Fuera del `with`: el medio ya se borro. Solo quedan metadatos.
        await self._state.record(item)
        report.published.append(item)
        report.record(RunOutcome.PUBLISHED)
        return True


#: Firma interna del avisador, ya con los argumentos por comodidad.
_Notifier = Callable[..., None]


def _make_notifier(on_progress: ProgressCallback | None) -> _Notifier:
    """Envuelve el callback para que el pipeline no tenga que comprobar None.

    Tambien lo aisla: un fallo en la interfaz que escucha no puede tumbar un
    run que por lo demas iba bien.
    """
    if on_progress is None:
        return lambda *_args, **_kwargs: None

    def _notify(
        stage: str, detail: str, *, current: int | None = None, total: int | None = None
    ) -> None:
        try:
            on_progress(ProgressEvent(stage=stage, detail=detail, current=current, total=total))
        except Exception as exc:  # el pipeline manda, no la interfaz
            log.warning("progress_callback_failed", error=str(exc))

    return _notify


def _build_dry_run_rows(
    selected: list[ScoredCandidate],
    rejected: list[tuple[RawCandidate, str]],
    low_score: list[ScoredCandidate],
) -> list[DryRunRow]:
    """Tabla explicativa de un `--dry-run`, ordenada por relevancia."""
    rows = [DryRunRow(item.candidate, item.score, "SELECCIONADO") for item in selected]
    rows += [DryRunRow(item.candidate, item.score, "nota baja") for item in low_score[:20]]
    rows += [DryRunRow(candidate, 0.0, reason) for candidate, reason in rejected[:20]]
    return rows
