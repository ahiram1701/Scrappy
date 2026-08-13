"""Horas escritas para leerlas, no para procesarlas.

Todo lo que Scrappy guarda va en UTC, sin excepcion: la base de datos, los
logs y las comparaciones de antiguedad. Mezclar zonas ahi es una fuente
clasica de errores que solo se manifiestan en marzo y en octubre.

Este modulo es la otra mitad: el punto -unico- donde una fecha deja de ser un
dato y pasa a ser algo que alguien lee. La conversion a la zona local ocurre
aqui y en ningun otro sitio.

Y se escribe como se diria en voz alta. «2026-08-13T05:00:00+00:00» obliga a
restar seis horas mentalmente para saber si eso es hoy o manana; «hoy a las
23:00» no obliga a nada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

__all__ = ["formato_local", "formato_relativo"]


def formato_local(momento: datetime, zona: tzinfo) -> str:
    """Una fecha en la zona del usuario, en lenguaje corriente.

    Devuelve «hoy a las 23:00», «manana a las 05:00» o «el 15/08 a las 05:00»
    segun lo lejos que quede. La referencia es *hoy* en esa misma zona, no en
    UTC: si no, un evento de las 23:00 locales saldria como «manana» durante
    seis horas al dia.
    """
    local = momento.astimezone(zona)
    hoy = datetime.now(zona).date()
    dias = (local.date() - hoy).days
    hora = local.strftime("%H:%M")

    match dias:
        case 0:
            return f"hoy a las {hora}"
        case 1:
            return f"manana a las {hora}"
        case -1:
            return f"ayer a las {hora}"
        case _:
            return f"el {local.strftime('%d/%m')} a las {hora}"


def formato_relativo(delta: timedelta) -> str:
    """Una duracion en la unidad que toque, sin decimales innecesarios.

    «en 3 h», «en 25 min», «ahora mismo». Un valor negativo -algo que ya
    deberia haber pasado- se dice como tal en vez de mostrar un menos, que en
    una interfaz se lee como un error.
    """
    segundos = delta.total_seconds()
    pasado = segundos < 0
    segundos = abs(segundos)

    if segundos < 60:
        return "hace un momento" if pasado else "ahora mismo"

    if segundos < 3600:
        cantidad, unidad = round(segundos / 60), "min"
    elif segundos < 86_400:
        cantidad, unidad = round(segundos / 3600), "h"
    else:
        cantidad, unidad = round(segundos / 86_400), "d"

    return f"hace {cantidad} {unidad}" if pasado else f"en {cantidad} {unidad}"
