"""Configurar las fuentes desde Telegram.

Activar una fuente y decidir de donde saca el contenido eran cosas que solo se
podian hacer delante del equipo. Con el arranque automatico puesto, Scrappy
corre sin ventana y el movil es el unico sitio desde donde se le habla, asi que
aqui esta la otra mitad de la administracion.

## Dos ficheros, y no es un capricho

El interruptor de cada fuente vive en el `.env` (`SCRAPPY_REDDIT_ENABLED`) y
sus origenes en `config/sources.yaml`. No se unifican porque significan cosas
distintas: el `.env` son las credenciales y los interruptores de esta maquina,
y el YAML es el catalogo de que buscar, que se comparte y se versiona.

La consecuencia practica es que **cada uno se aplica de una forma**: el
catalogo lo relee el pipeline en cada ronda -y desde que los adapters se
refrescan con el, tambien los origenes-, mientras que el `.env` solo entra
reconstruyendo la aplicacion. De ahi que tocar un origen conteste al momento y
tocar el interruptor pida una recarga.

## Por que los `callback_data` van con indices

`callback_data` no pasa de 64 bytes. Una consulta de Bluesky ocupa ella sola
mas de la mitad, y un subreddit largo con su prefijo se acerca al limite, asi
que aqui viajan **posiciones**, no nombres: `sq:reddit:0:7`. A cambio, un
indice puede quedarse viejo si alguien edita el YAML entre que se pinta el
teclado y se pulsa, y por eso quitar siempre pasa por una confirmacion que
enseña el valor releido del fichero.
"""

from __future__ import annotations

import re

from scrappy.sources.registry import iter_adapter_names, requiere_ack_de_tos

#: De donde saca el contenido cada fuente: clave en el YAML y como se llama al
#: hablar de ella. El **orden importa y es un contrato**: los `callback_data`
#: llevan la posicion del campo, asi que reordenar esta tabla haria que un
#: boton ya enviado apuntara a otro sitio. Anadir al final es seguro.
ORIGENES: dict[str, tuple[tuple[str, str], ...]] = {
    "reddit": (("subreddits", "subreddits"),),
    "lemmy": (("communities", "comunidades"),),
    "bluesky": (("queries", "busquedas"),),
    "imgur": (("tags", "etiquetas"),),
    "giphy": (("queries", "busquedas"),),
    "youtube": (("queries", "busquedas"), ("channels", "canales")),
    # X es la unica con dos backends, y cada uno lee un campo distinto: con
    # `scrape` las busquedas no las mira nadie. Sin decirlo en la etiqueta, uno
    # se pasa un rato anadiendo consultas que no hacen nada.
    "x": (("queries", "busquedas (backend api)"), ("accounts", "cuentas (backend scrape)")),
    "tiktok": (("hashtags", "hashtags"), ("accounts", "cuentas")),
    "instagram": (("hashtags", "hashtags"), ("accounts", "cuentas")),
}

#: Tope de elementos por lista. No es un limite de Scrappy sino del teclado de
#: Telegram, que con demasiados botones deja de ser usable en un movil. Quien
#: necesite mas tiene la TUI y el fichero.
MAXIMO_POR_LISTA = 50

#: Un subreddit valido. Se comprueba porque `SourceConfig` acepta cualquier
#: clave extra sin validar: el esquema deja pasar «r/ memes», y el fallo no se
#: ve hasta la ronda siguiente, en un log que nadie esta mirando.
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
#: Una comunidad de Lemmy es `nombre@instancia`.
_COMUNIDAD = re.compile(r"^[A-Za-z0-9_]{2,50}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def campos_de(fuente: str) -> tuple[tuple[str, str], ...]:
    """Los campos de origen de una fuente, o vacio si no se conoce."""
    return ORIGENES.get(fuente, ())


def clave_env(fuente: str) -> str:
    """La variable del `.env` que enciende y apaga esa fuente."""
    return f"SCRAPPY_{fuente.upper()}_ENABLED"


def fuente_conocida(fuente: str) -> bool:
    """Si ese nombre es una de las nueve fuentes. Los callbacks vienen de
    fuera, y aunque solo los pulsen administradores, un nombre inventado no
    debe llegar a construir una ruta de YAML."""
    return fuente in set(iter_adapter_names())


class ValorNoValido(ValueError):
    """Lo que se escribio no sirve para ese campo, y se dice por que."""


def normalizar(campo: str, valor: str) -> str:
    """Deja el valor como lo espera el adapter, o explica por que no vale.

    La gente escribe «r/memes», «#gatos» o «@cuenta» porque es como se ven en
    la plataforma, y guardarlo tal cual produce una URL rota que no se nota
    hasta tres horas despues, en la ronda siguiente y en un log.
    """
    limpio = valor.strip().strip(",").strip()
    limpio = re.sub(r"^(?:https?://)?(?:www\.)?reddit\.com/", "", limpio)
    limpio = re.sub(r"^/?r/", "", limpio)
    limpio = limpio.lstrip("#@").strip().rstrip("/")

    if not limpio:
        raise ValorNoValido("esta vacio")
    if "\n" in limpio or len(limpio) > 200:
        raise ValorNoValido("es demasiado largo")

    match campo:
        case "subreddits":
            if not _SUBREDDIT.match(limpio):
                raise ValorNoValido(
                    "un subreddit solo lleva letras, numeros y guion bajo (2-21 caracteres)"
                )
        case "communities":
            if not _COMUNIDAD.match(limpio):
                raise ValorNoValido("una comunidad de Lemmy se escribe «nombre@instancia»")
        case "channels" | "accounts":
            if " " in limpio:
                raise ValorNoValido("una cuenta no lleva espacios")

    return limpio


def ya_esta(valores: list[str], nuevo: str) -> bool:
    """Si ese valor ya esta en la lista, sin distinguir mayusculas.

    Igual que el veto de autores: pulsar dos veces no puede ser un error ni
    dejar la lista con el mismo sitio escrito de dos formas.
    """
    return nuevo.casefold() in {str(v).casefold() for v in valores}


__all__ = [
    "MAXIMO_POR_LISTA",
    "ORIGENES",
    "ValorNoValido",
    "campos_de",
    "clave_env",
    "fuente_conocida",
    "normalizar",
    "requiere_ack_de_tos",
    "ya_esta",
]
