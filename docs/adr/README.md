# Registro de decisiones de arquitectura

Un ADR documenta una decisión técnica, el contexto en el que se tomó y **qué alternativas se descartaron y por qué**. Esa última parte es la que se pierde siempre y la que más se echa de menos seis meses después.

Formato: contexto → decisión → consecuencias.

| # | Decisión | Estado |
|---|---|---|
| [0001](0001-python-y-async.md) | Python asíncrono como base | Aceptada |
| [0002](0002-httpx-en-vez-de-praw.md) | httpx directo en vez de PRAW | Sustituida por [0009](0009-reddit-por-rss.md) |
| [0003](0003-x-doble-backend.md) | Doble backend para X | Aceptada |
| [0004](0004-sqlite-sin-orm.md) | SQLite con SQL a mano, sin ORM | Aceptada |
| [0005](0005-ranking-por-percentiles.md) | Ranking por percentiles, no por métricas brutas | Aceptada |
| [0006](0006-dedup-perceptual.md) | Deduplicación perceptual con pHash de frames | Aceptada |
| [0007](0007-fuentes-tos-desactivadas.md) | Fuentes que incumplen ToS, desactivadas por defecto | Aceptada |
| [0008](0008-almacenamiento-efimero.md) | Almacenamiento efímero: Telegram como único archivo | Aceptada |
| [0009](0009-reddit-por-rss.md) | Reddit por feeds Atom en vez de la API oficial | Aceptada |
| [0010](0010-tui-con-textual.md) | TUI con Textual, y ruamel para editar la configuración | Aceptada |

Al añadir uno nuevo, numera correlativamente y añádelo a esta tabla.
