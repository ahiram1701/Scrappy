# ADR-0004: SQLite con SQL a mano, sin ORM

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

Hay que recordar qué se ha publicado para no repetirlo. El volumen es minúsculo: unos 200 bytes por item, del orden de 15.000 filas al año publicando 40 al día. Las consultas son cuatro:

- ¿existe este `uid`?
- ¿existe este `sha256`?
- dame los `phash` de los últimos 90 días
- cuenta por fuente desde una fecha

Un ORM está pensado para esquemas que evolucionan, relaciones complejas y consultas dinámicas. Aquí no hay nada de eso.

## Decisión

**`aiosqlite` con SQL escrito a mano** y migraciones versionadas mediante `PRAGMA user_version`.

```python
_MIGRATIONS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS published (...); CREATE INDEX ...;""",
)
```

Al arrancar se lee `user_version` y se aplican en orden las migraciones que falten. Añadir una es añadir una cadena a la tupla.

Además, el estado se abstrae tras `StateBackendProtocol`, con tres implementaciones: `sqlite`, `memory` y `none`. El pipeline nunca sabe cuál está activa, y eso es lo que permite ofrecer tres niveles de privacidad sin ramificar la lógica ([ADR-0008](0008-almacenamiento-efimero.md)).

## Alternativas descartadas

**SQLAlchemy 2.0 async + Alembic.** Es lo que usaría en un proyecto con un modelo de datos de verdad. Aquí serían dos dependencias pesadas, un directorio de migraciones y un `env.py` para gestionar **una tabla**. La complejidad no la paga nadie.

**Un JSON o un fichero de líneas.** Más simple todavía, pero `has_sha256` sobre 15.000 entradas sin índice, en cada item de cada ejecución, se degrada rápido. SQLite da índices gratis.

**Redis.** Perfecto para este patrón de acceso, pero añade un servicio que administrar para guardar 3 MB. Desproporcionado para un bot que corre en una Raspberry.

**Sin persistencia.** Se ofrece como opción (`STATE_BACKEND=none`) pero no como defecto: sin memoria, el bot repite el mismo meme cada tres horas.

## Consecuencias

- El esquema y las consultas están a la vista, en un fichero, sin capas intermedias.
- Las migraciones son manuales. Con una tabla es asumible; si el esquema creciera de verdad, este ADR habría que revisarlo.
- `PRAGMA journal_mode=WAL` evita bloqueos entre el scheduler y los comandos del bot, que corren en el mismo proceso pero en tareas distintas.
- Los tests ejecutan **el mismo juego de pruebas contra `sqlite` y `memory`**, lo que garantiza que son de verdad intercambiables y no solo de nombre.
