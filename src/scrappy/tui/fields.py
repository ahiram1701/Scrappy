"""Declaracion de los campos configurables.

Son unos cincuenta valores repartidos entre el `.env` y `sources.yaml`. Escribir
un widget a mano por cada uno seria mil lineas de copia y pega que se
desincronizarian con `settings.py` a la primera opcion nueva.

En vez de eso se declaran como datos y la pantalla los pinta de forma generica.
Anadir un ajuste a la interfaz pasa a ser una linea aqui.

El texto de `help` importa tanto como el campo: es lo unico que explica *por
que* tocar ese valor, y casi todo sale de cosas que costo descubrir probando
contra las APIs reales.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FieldKind(StrEnum):
    """Como se pinta y como se convierte de vuelta."""

    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    CHOICE = "choice"
    #: Lista de cadenas, editada separada por comas.
    LIST = "list"


@dataclass(frozen=True, slots=True)
class EnvField:
    """Un ajuste del `.env`."""

    key: str
    label: str
    help: str = ""
    kind: FieldKind = FieldKind.TEXT
    choices: tuple[str, ...] = ()

    @property
    def widget_id(self) -> str:
        return f"env-{self.key}"


@dataclass(frozen=True, slots=True)
class YamlField:
    """Un ajuste de `sources.yaml`, identificado por su ruta de claves."""

    path: tuple[str, ...]
    label: str
    help: str = ""
    kind: FieldKind = FieldKind.NUMBER

    @property
    def widget_id(self) -> str:
        # Los puntos no valen como id en Textual.
        return "yaml-" + "__".join(self.path)


# ===========================================================================
# Pestanas del .env
# ===========================================================================
TELEGRAM_FIELDS = (
    EnvField(
        "SCRAPPY_TELEGRAM_BOT_TOKEN",
        "Token del bot",
        "Te lo da @BotFather con /newbot. Un solo ':' en todo el token.",
    ),
    EnvField(
        "SCRAPPY_TELEGRAM_TARGET_CHAT_ID",
        "Chat destino",
        "Tu id de usuario POSITIVO para el privado, o el del canal empezando "
        "por -100. Un menos de mas da «Chat not found».",
    ),
    EnvField(
        "SCRAPPY_TELEGRAM_ADMIN_IDS",
        "Administradores",
        "Ids separados por coma que pueden usar los comandos. Vacio = nadie.",
    ),
)

CONTENT_FIELDS = (
    EnvField(
        "SCRAPPY_ALLOW_NSFW",
        "Permitir NSFW",
        kind=FieldKind.BOOL,
    ),
    EnvField(
        "SCRAPPY_MIN_DURATION_SECONDS",
        "Duracion minima (s)",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_MAX_DURATION_SECONDS",
        "Duracion maxima (s)",
        "Por encima de esto se descarta. Tambien hay una penalizacion en el "
        "ranking, en la pestana Ranking.",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_MAX_AGE_HOURS",
        "Antiguedad maxima (h)",
        "Ojo con apretarlo: el feed de Reddit viene en orden `hot` e incluye "
        "posts de varios dias, asi que un valor bajo descarta buena parte.",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_PHASH_THRESHOLD",
        "Umbral de pHash",
        "Distancia maxima para considerar dos medios el mismo. Subirlo detecta "
        "mas reposts; pasarse junta cosas distintas.",
        kind=FieldKind.NUMBER,
    ),
)

SCHEDULE_FIELDS = (
    EnvField(
        "SCRAPPY_NOTIFY_ON_START",
        "Avisar al arrancar",
        "Te escribe por Telegram cuando Scrappy arranca. Util con el arranque "
        "automatico, que se levanta sin ventana: es como saber que sigue vivo "
        "despues de reiniciar sin mirar el log. Va a los administradores.",
        kind=FieldKind.BOOL,
    ),
    EnvField("SCRAPPY_SCHEDULE_ENABLED", "Scheduler activo", kind=FieldKind.BOOL),
    EnvField(
        "SCRAPPY_SCHEDULE_INTERVAL_MINUTES",
        "Intervalo (min)",
        "Cada cuanto se ejecuta el pipeline automaticamente.",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_ITEMS_PER_RUN",
        "Items por ronda",
        "Cuantos se publican como maximo en cada ejecucion.",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_TIMEZONE",
        "Zona horaria",
        "En formato IANA, como America/Mexico_City. Vacio = la del sistema. "
        "Solo afecta a la hora de disparo y a las que se muestran: lo que se "
        "guarda sigue siendo UTC.",
    ),
)

STORAGE_FIELDS = (
    EnvField(
        "SCRAPPY_STATE_BACKEND",
        "Backend de estado",
        "Solo afecta a los metadatos de deduplicacion; el contenido nunca se "
        "guarda. `memory` no toca el disco pero repite entre ejecuciones.",
        kind=FieldKind.CHOICE,
        choices=("sqlite", "memory", "none"),
    ),
    EnvField(
        "SCRAPPY_MAX_DOWNLOAD_MB",
        "Descarga maxima (MB)",
        "El limite real de subida a Telegram son 50 MB.",
        kind=FieldKind.NUMBER,
    ),
    EnvField(
        "SCRAPPY_LOG_LEVEL",
        "Nivel de log",
        kind=FieldKind.CHOICE,
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    EnvField(
        "SCRAPPY_ENABLE_TOS_RISKY_SOURCES",
        "Permitir fuentes con riesgo de ToS",
        "YouTube, TikTok, Instagram y X-scrape incumplen los terminos de sus "
        "plataformas. Lee docs/LEGAL.md antes de activarlo.",
        kind=FieldKind.BOOL,
    ),
)

# ===========================================================================
# Pestanas de sources.yaml
# ===========================================================================
RANKING_FIELDS = (
    YamlField(
        ("ranking", "weights", "engagement"),
        "Peso: engagement",
        "Cuanto pesa destacar dentro de su propio lote. Los tres pesos deberian sumar 1.0.",
    ),
    YamlField(
        ("ranking", "weights", "velocity"),
        "Peso: velocidad",
        "Cuanto pesa hacerse viral rapido. Subirlo trae cosas mas frescas.",
    ),
    YamlField(
        ("ranking", "weights", "source"), "Peso: fuente", "Tu preferencia manual por plataforma."
    ),
    YamlField(
        ("ranking", "min_score"),
        "Nota minima",
        "Por debajo de esto ni se descarga. Subirlo filtra morralla; "
        "bajarlo hace que publique mas.",
    ),
    YamlField(("ranking", "penalties", "too_long"), "Penalizacion: muy largo"),
    YamlField(("ranking", "penalties", "too_short"), "Penalizacion: muy corto"),
    YamlField(("ranking", "penalties", "no_thumbnail"), "Penalizacion: sin miniatura"),
    YamlField(
        ("ranking", "penalties", "low_comment_ratio"),
        "Penalizacion: poca conversacion",
        "Muchos likes y ningun comentario suele indicar engagement inflado.",
    ),
    YamlField(
        ("ranking", "min_comment_ratio"),
        "Ratio minimo de comentarios",
        "Por debajo se aplica la penalizacion anterior, y solo a partir de 1.000 de engagement.",
    ),
)

FILTER_FIELDS = (
    YamlField(
        ("filters", "blocked_keywords"),
        "Palabras vetadas",
        "Separadas por comas. Se buscan en el titulo, sin distinguir mayusculas.",
        kind=FieldKind.LIST,
    ),
    YamlField(
        ("filters", "blocked_authors"),
        "Autores vetados",
        "Sin la @. Tambien se rellena desde el boton «Vetar autor» de Telegram.",
        kind=FieldKind.LIST,
    ),
    YamlField(
        ("filters", "allowed_languages"),
        "Idiomas permitidos",
        "Codigos como es, en. Vacio = no filtrar por idioma.",
        kind=FieldKind.LIST,
    ),
)

DELIVERY_FIELDS = (
    YamlField(
        ("delivery", "show_score"),
        "Mostrar el score",
        "Util para calibrar; molesto en un canal publico.",
        kind=FieldKind.BOOL,
    ),
    YamlField(("delivery", "show_source_badge"), "Mostrar insignia de fuente", kind=FieldKind.BOOL),
    YamlField(
        ("delivery", "silent_notifications"), "Notificaciones silenciosas", kind=FieldKind.BOOL
    ),
    YamlField(
        ("delivery", "delay_between_posts"),
        "Espera entre publicaciones (s)",
        "Telegram limita a ~20 mensajes por minuto y por chat.",
    ),
)

# ===========================================================================
# Ajustes propios de cada fuente
# ===========================================================================
#: Clave del flag `*_ENABLED` de cada fuente, para el interruptor de su pestana.
SOURCE_ENABLED_KEY = {
    name: f"SCRAPPY_{name.upper()}_ENABLED"
    for name in (
        "reddit",
        "lemmy",
        "bluesky",
        "imgur",
        "giphy",
        "youtube",
        "x",
        "tiktok",
        "instagram",
    )
}

#: Fuentes que necesitan el flag de ToS. Se avisa en su pestana.
#:
#: `x` no esta aqui a proposito: depende de su backend, y quien lo decide es
#: `registry.requiere_ack_de_tos`. Su aviso se compone aparte, en la pantalla.
TOS_RISKY = frozenset({"youtube", "tiktok", "instagram"})

#: Ajustes del `.env` propios de una fuente, mas alla de su interruptor.
#:
#: Las credenciales siguen sin estar en la TUI -es el criterio del proyecto-,
#: pero el backend de X no es una credencial: es el ajuste que decide si la
#: fuente usa la via oficial o la que incumple los terminos, y tenerlo invisible
#: hacia que la pestana no dijera lo que de verdad estaba pasando.
SOURCE_EXTRA_ENV: dict[str, tuple[EnvField, ...]] = {
    "x": (
        EnvField(
            "SCRAPPY_X_BACKEND",
            "Backend",
            "`api` es la via oficial, pero buscar exige el tier Basic de pago. "
            "`scrape` es gratis e incumple los terminos de X.",
            kind=FieldKind.CHOICE,
            choices=("api", "scrape"),
        ),
        EnvField(
            "SCRAPPY_X_COOKIES_FILE",
            "Fichero de cookies",
            "Solo para el backend `scrape`, en formato Netscape. Que salga de "
            "una CUENTA DESECHABLE: X bloquea la cuenta cuyas cookies se usen.",
        ),
    ),
}

_COMUNES = (
    ("weight", "Peso de la fuente", "Tu preferencia manual, de 0 a 1.", FieldKind.NUMBER),
    (
        "budget",
        "Candidatos a pedir",
        "Antes de filtrar y rankear. Pedir de mas cuesta poco y mejora la seleccion.",
        FieldKind.NUMBER,
    ),
)

#: Claves propias de cada fuente, mas alla de las comunes.
SOURCE_EXTRA_FIELDS: dict[str, tuple[tuple[str, str, str, FieldKind], ...]] = {
    "reddit": (
        ("subreddits", "Subreddits", "Sin r/, separados por comas.", FieldKind.LIST),
        (
            "subreddits_per_run",
            "Subreddits por ronda",
            "No se consultan todos cada vez: el rate limit sin autenticar no lo "
            "permite. Se rotan entre ejecuciones.",
            FieldKind.NUMBER,
        ),
        ("delay_seconds", "Espera entre subreddits (s)", "Bajarlo provoca 429.", FieldKind.NUMBER),
    ),
    "lemmy": (
        ("instance", "Instancia", "Cualquier instancia de Lemmy sirve.", FieldKind.TEXT),
        ("sort", "Orden", "TopDay, TopSixHour, Hot, Active…", FieldKind.TEXT),
        (
            "communities",
            "Comunidades",
            "Formato nombre@instancia. Vacio = portada.",
            FieldKind.LIST,
        ),
        ("delay_seconds", "Espera entre comunidades (s)", "", FieldKind.NUMBER),
    ),
    "bluesky": (
        ("queries", "Consultas", "Terminos de busqueda, separados por comas.", FieldKind.LIST),
        (
            "window_hours",
            "Ventana (h)",
            "IMPORTANTE: sin acotar, la busqueda por `top` devuelve lo mas votado "
            "de SIEMPRE, no lo que se mueve ahora.",
            FieldKind.NUMBER,
        ),
        ("delay_seconds", "Espera entre consultas (s)", "", FieldKind.NUMBER),
    ),
    "imgur": (
        ("tags", "Etiquetas", "La galeria viral se consulta siempre.", FieldKind.LIST),
        ("window", "Ventana", "day, week o month.", FieldKind.TEXT),
        ("delay_seconds", "Espera (s)", "", FieldKind.NUMBER),
    ),
    "giphy": (
        ("queries", "Busquedas", "Las tendencias se consultan siempre.", FieldKind.LIST),
        ("rating", "Clasificacion", "g, pg, pg-13 o r.", FieldKind.TEXT),
        ("delay_seconds", "Espera (s)", "", FieldKind.NUMBER),
    ),
    "youtube": (
        ("queries", "Busquedas", "", FieldKind.LIST),
        ("channels", "Canales", "Sin la @.", FieldKind.LIST),
        ("results_per_query", "Resultados por busqueda", "", FieldKind.NUMBER),
        (
            "max_duration",
            "Duracion maxima (s)",
            "YouTube admite Shorts de hasta 3 minutos.",
            FieldKind.NUMBER,
        ),
        ("min_view_count", "Visualizaciones minimas", "", FieldKind.NUMBER),
    ),
    "x": (
        ("queries", "Consultas (backend api)", "Sintaxis de la API v2 de X.", FieldKind.LIST),
        ("accounts", "Cuentas (backend scrape)", "Sin la @.", FieldKind.LIST),
        (
            "objetivos_por_ronda",
            "Perfiles por ronda",
            "Con el backend `scrape`, esto ES la proteccion. Un perfil cada "
            "cuatro horas se parece a alguien mirando X; cuatro seguidos, no. "
            "Se rotan, asi que la lista entera se cubre igual.",
            FieldKind.NUMBER,
        ),
        (
            "delay_seconds",
            "Espera entre perfiles (s)",
            "Solo aplica cuando toca mas de uno. Bajarlo es lo que te marca.",
            FieldKind.NUMBER,
        ),
    ),
    "tiktok": (
        ("hashtags", "Hashtags", "Sin la #.", FieldKind.LIST),
        ("accounts", "Cuentas", "Sin la @.", FieldKind.LIST),
        (
            "min_play_count",
            "Reproducciones minimas",
            "En TikTok la escala es un orden de magnitud mayor que en otras plataformas.",
            FieldKind.NUMBER,
        ),
    ),
    "instagram": (
        ("hashtags", "Hashtags", "", FieldKind.LIST),
        ("accounts", "Cuentas", "", FieldKind.LIST),
    ),
}


def fields_for_source(source: str) -> tuple[YamlField, ...]:
    """Campos de una fuente: los comunes mas los suyos propios."""
    especificos = SOURCE_EXTRA_FIELDS.get(source, ())
    return tuple(
        YamlField(("sources", source, clave), etiqueta, ayuda, kind)
        for clave, etiqueta, ayuda, kind in (*_COMUNES, *especificos)
    )
