"""Los botones del menu de fuentes.

Van en su propio modulo y no en `callbacks.py` porque son casi la mitad de los
botones del bot y tienen una regla propia que conviene tener escrita en un solo
sitio: **cada pantalla se relee del fichero antes de pintarse**.

No es prudencia excesiva. Los `callback_data` llevan indices, no nombres (ver
`bot/fuentes.py`), asi que un teclado enviado hace media hora apunta a
posiciones que pueden haber cambiado si alguien edito el YAML por otro lado.
Releer y comprobar el rango convierte eso en un mensaje claro en vez de en un
borrado silencioso del elemento equivocado.

## Que se aplica al momento y que pide recarga

- **Origenes**: se escriben en `sources.yaml` y se asignan a `app.sources_config`,
  que refresca el filtro y los adapters. Surte efecto ya, sin recargar nada.
- **Interruptor**: vive en el `.env`, que solo se lee al construir la
  aplicacion. Ahi no hay atajo: se pide una recarga y se avisa al volver.
"""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Any

from telegram import ForceReply, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from scrappy.app import ScrappyApp
from scrappy.bot.fuentes import (
    MAXIMO_POR_LISTA,
    ValorNoValido,
    campos_de,
    clave_env,
    fuente_conocida,
    normalizar,
    ya_esta,
)
from scrappy.bot.keyboards import (
    confirmar_encender_arriesgada,
    confirmar_quitar,
    ficha_fuente,
    lista_origenes,
    menu_fuentes,
)
from scrappy.bot.recarga import pedir_recarga
from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger
from scrappy.sources.registry import iter_adapter_names, requiere_ack_de_tos

if TYPE_CHECKING:
    from scrappy.tui.yaml_editor import SourcesYamlEditor

log = get_logger(__name__)

#: Con que empieza el mensaje que pide escribir un valor. Sirve para reconocer
#: una respuesta a un aviso que ya no esta correlacionado -tras una recarga, el
#: `user_data` se pierde- y poder explicarlo en vez de callarse.
PROMPT = "✏️"

#: Donde se apunta a que pregunta corresponde cada respuesta.
CLAVE_PENDIENTES = "fuentes_pendientes"


def _editor(app: ScrappyApp) -> SourcesYamlEditor:
    """Un editor cargado sobre el YAML que este usando la aplicacion.

    El import va dentro y no arriba porque los editores viven en el paquete de
    la TUI, y la TUI importa el bot: a nivel de modulo esto seria un ciclo. Es
    la misma solucion que ya usa el boton de vetar autores. Lo correcto seria
    que los editores no vivieran bajo `tui/`, pero moverlos es otra tanda.
    """
    from scrappy.tui.yaml_editor import SourcesYamlEditor

    editor = SourcesYamlEditor(app.settings.sources_config_path)
    editor.load()
    return editor


def _valores(editor: SourcesYamlEditor, fuente: str, campo: str) -> list[str] | None:
    """Los valores de un campo, o None si la clave no esta en el fichero.

    La distincion importa: el editor no crea claves que no existan, asi que
    «no esta» significa que aqui no se va a poder escribir, y hay que decirlo
    en vez de ofrecer un boton que no funcionara.
    """
    actual = editor.get_value(["sources", fuente, campo])
    if actual is None:
        return None
    return [str(v) for v in actual]


async def _pintar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """La lista de fuentes, con su estado."""
    app = _app_de(context)
    # `arriesgada` va sin restarle el flag a proposito: el candado desaparece al
    # activarlo, pero la fuente sigue siendo la que es y el menu tiene que
    # seguir diciendolo.
    estados = [
        (
            nombre,
            bool(getattr(app.settings, f"{nombre}_enabled", False)),
            requiere_ack_de_tos(nombre, app.settings) and not app.settings.enable_tos_risky_sources,
            requiere_ack_de_tos(nombre, app.settings),
        )
        for nombre in iter_adapter_names()
    ]

    query = update.callback_query
    assert query is not None
    await query.edit_message_text(
        "<b>Fuentes</b>\nElige una para encenderla, apagarla o cambiar de donde saca el contenido.",
        parse_mode=ParseMode.HTML,
        reply_markup=menu_fuentes(estados),
    )


async def _pintar_ficha(update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str) -> None:
    """La ficha de una fuente: como esta y que campos tiene."""
    app = _app_de(context)
    editor = _editor(app)
    campos = campos_de(fuente)

    cuantos: dict[str, int | None] = {}
    for clave, _ in campos:
        valores = _valores(editor, fuente, clave)
        cuantos[clave] = None if valores is None else len(valores)

    estado = next((s for s in await app.source_statuses() if s.name == fuente), None)
    detalle = estado.render() if estado is not None else fuente

    query = update.callback_query
    assert query is not None
    await query.edit_message_text(
        f"<b>{fuente}</b>\n{detalle}",
        parse_mode=ParseMode.HTML,
        reply_markup=ficha_fuente(
            fuente,
            encendida=bool(getattr(app.settings, f"{fuente}_enabled", False)),
            campos=campos,
            cuantos=cuantos,
            renovable=_tiene_sesion_renovable(fuente, app),
        ),
    )


async def _pintar_origenes(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str, campo: int
) -> None:
    """La lista de valores de un campo, releida del fichero."""
    app = _app_de(context)
    campos = campos_de(fuente)
    clave, etiqueta = campos[campo]
    valores = _valores(_editor(app), fuente, clave)

    query = update.callback_query
    assert query is not None

    if valores is None:
        await query.edit_message_text(
            f"<b>{fuente} · {etiqueta}</b>\nNo hay <code>sources.{fuente}.{clave}</code> en "
            "sources.yaml, asi que desde aqui no se puede escribir. Anadelo al fichero "
            "con su comentario y vuelve.",
            parse_mode=ParseMode.HTML,
            reply_markup=ficha_fuente(
                fuente,
                encendida=bool(getattr(app.settings, f"{fuente}_enabled", False)),
                campos=campos,
                cuantos={clave: None},
            ),
        )
        return

    # Con muchos valores el teclado deja de ser usable en un movil. Se ensenan
    # los primeros y se dice cuantos quedan, en vez de pintar una pared.
    visibles = valores[:40]
    sobran = len(valores) - len(visibles)
    cabecera = f"<b>{fuente} · {etiqueta}</b> ({len(valores)})"
    if sobran:
        cabecera += f"\nSe ensenan los {len(visibles)} primeros; hay {sobran} mas en el fichero."

    await query.edit_message_text(
        cabecera,
        parse_mode=ParseMode.HTML,
        reply_markup=lista_origenes(
            fuente, campo, visibles, se_puede_anadir=len(valores) < MAXIMO_POR_LISTA
        ),
    )


def _tiene_sesion_renovable(fuente: str, app: ScrappyApp) -> bool:
    """Si esa fuente vive de una sesion que Scrappy pueda reextraer.

    Hoy solo X con el backend `scrape`. TikTok e Instagram tambien usan cookies,
    pero su flujo es otro y estan apagadas; cuando toque, esto es una linea y no
    un refactor.
    """
    from scrappy.config.settings import XBackend

    return fuente == "x" and app.settings.x_backend is XBackend.SCRAPE


async def _renovar_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str) -> None:
    """Reextrae la sesion de X del navegador del equipo, desde el movil.

    Es el boton que evita tener que sentarse delante del ordenador a recordar
    una invocacion de yt-dlp con la ruta de un perfil que no se llama como uno
    cree. Lo hace el mismo codigo que `scrappy cookies` y que la TUI, asi que
    los tres contestan exactamente lo mismo.
    """
    from scrappy.sources.x_cookies import renovar_cookies

    app = _app_de(context)
    query = update.callback_query
    assert query is not None

    if not _tiene_sesion_renovable(fuente, app):
        await query.answer("Esa fuente no usa cookies renovables.", show_alert=True)
        return

    await query.answer("Renovando...")
    # Lee una base de datos del navegador: bloquea, y el bucle del bot no puede
    # quedarse parado mientras tanto.
    resultado = await asyncio.to_thread(renovar_cookies, app.settings)
    log.info("cookies_renovadas_desde_telegram", ok=resultado.ok)

    campos = campos_de(fuente)
    await query.edit_message_text(
        f"{'✅' if resultado.ok else '⚠️'} <b>{fuente}</b>\n{html.escape(resultado.detalle)}",
        parse_mode=ParseMode.HTML,
        reply_markup=ficha_fuente(
            fuente,
            encendida=bool(getattr(app.settings, f"{fuente}_enabled", False)),
            campos=campos,
            cuantos=dict.fromkeys((clave for clave, _ in campos), None),
            renovable=True,
        ),
    )


async def _confirmar_encender(update: Update, fuente: str) -> None:
    """Pregunta antes de encender una fuente que incumple los ToS.

    El texto dice lo que se pierde de vista con el tiempo: que el flag global
    se acepto una vez y sigue puesto, y que estas fuentes no son iguales que
    las demas por mucho que el menu las pinte en la misma cuadricula.
    """
    query = update.callback_query
    assert query is not None

    await query.answer()
    await query.edit_message_text(
        f"⚠️ <b>{fuente}</b> obtiene el contenido incumpliendo los terminos de su "
        "plataforma. Puede acarrear el bloqueo de tu IP o de la cuenta cuyas cookies "
        "use, y se rompera cada pocas semanas.\n\n"
        "Lo permite el flag que ya aceptaste en el <code>.env</code>. Encenderla?",
        parse_mode=ParseMode.HTML,
        reply_markup=confirmar_encender_arriesgada(fuente),
    )


async def _confirmar_quitar(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str, campo: int, indice: int
) -> None:
    """Pregunta antes de quitar, enseñando el valor releido del fichero."""
    app = _app_de(context)
    clave, etiqueta = campos_de(fuente)[campo]
    valores = _valores(_editor(app), fuente, clave) or []

    query = update.callback_query
    assert query is not None

    if indice >= len(valores):
        await query.answer("Ese valor ya no esta; la lista cambio.", show_alert=True)
        await _pintar_origenes(update, context, fuente, campo)
        return

    aviso = ""
    if len(valores) == 1:
        # No se prohibe: para eso esta el interruptor. Pero conviene saberlo.
        aviso = f"\n\n{fuente} se quedaria sin {etiqueta} y no traeria nada."

    await query.edit_message_text(
        f"Quitar <code>{valores[indice]}</code> de {fuente}.{clave}?{aviso}",
        parse_mode=ParseMode.HTML,
        reply_markup=confirmar_quitar(fuente, campo, indice),
    )


async def _quitar(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str, campo: int, indice: int
) -> None:
    """Quita el valor y repinta la lista."""
    app = _app_de(context)
    clave, _ = campos_de(fuente)[campo]
    editor = _editor(app)
    valores = _valores(editor, fuente, clave) or []

    query = update.callback_query
    assert query is not None

    if indice >= len(valores):
        await query.answer("Ese valor ya no esta; la lista cambio.", show_alert=True)
        await _pintar_origenes(update, context, fuente, campo)
        return

    quitado = valores.pop(indice)
    if not await _guardar(update, editor, fuente, clave, valores, app):
        return

    log.info("origen_quitado", fuente=fuente, campo=clave, valor=quitado)
    await query.answer(f"Quitado {quitado}.")
    await _pintar_origenes(update, context, fuente, campo)


async def _pedir_valor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fuente: str, campo: int
) -> None:
    """Pide por ForceReply el valor nuevo y apunta a que pregunta corresponde."""
    clave, etiqueta = campos_de(fuente)[campo]
    query = update.callback_query
    assert query is not None

    # Telegram entrega el mensaje como «inaccesible» si es muy viejo o si el bot
    # no puede leerlo. No se puede responder a algo asi, y ForceReply necesita
    # justo eso: un mensaje al que contestar.
    mensaje = query.message
    if not isinstance(mensaje, Message):
        await query.answer(
            "Este menu es demasiado viejo para anadir nada. Abre /sources otra vez.",
            show_alert=True,
        )
        return

    aviso = await mensaje.reply_text(
        f"{PROMPT} Escribe que anadir a <b>{fuente} · {etiqueta}</b>.\n"
        "Responde a este mensaje. Puedes poner varios, uno por linea.",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True),
    )

    if context.user_data is None:  # pragma: no cover - solo en updates sin usuario
        await query.answer("No se de quien viene esto.", show_alert=True)
        return
    pendientes: dict[int, tuple[str, int]] = context.user_data.setdefault(CLAVE_PENDIENTES, {})
    pendientes[aviso.message_id] = (fuente, campo)
    await query.answer()


async def _interruptor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    fuente: str,
    encender: bool,
    *,
    confirmado: bool = False,
) -> None:
    """Enciende o apaga una fuente escribiendo en el `.env`, y pide recarga."""
    app = _app_de(context)
    query = update.callback_query
    assert query is not None

    arriesgada = requiere_ack_de_tos(fuente, app.settings)
    if encender and arriesgada and not app.settings.enable_tos_risky_sources:
        # Aqui no se escribe nada a proposito. El flag de ToS es un
        # consentimiento informado, y un consentimiento que se da pulsando un
        # boton en el movil sin leer nada no es un consentimiento.
        await query.answer(
            f"{fuente} obtiene contenido incumpliendo los terminos de su plataforma. "
            "Para activarla hay que poner SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true a mano "
            "en el .env, despues de leer docs/LEGAL.md.",
            show_alert=True,
        )
        return

    if encender and arriesgada and not confirmado:
        # Con el flag ya puesto -y se pone una vez, para siempre- encender una
        # de estas quedaba a un toque de encender Reddit. Se recupera el paso
        # que el flag hacia antes.
        await _confirmar_encender(update, fuente)
        return

    from scrappy.tui.env_editor import EnvEditor  # ciclo: ver `_editor`

    editor = EnvEditor(app.env_path)
    editor.load()
    editor.set_value(clave_env(fuente), "true" if encender else "false")
    try:
        editor.save()
    except ConfigError as exc:
        await query.answer(f"No se pudo guardar: {exc}"[:200], show_alert=True)
        return

    verbo = "activo" if encender else "apago"
    log.info("fuente_conmutada", fuente=fuente, encendida=encender)

    if pedir_recarga(context, f"se {verbo} {fuente}"):
        await query.answer(f"{fuente}: guardado. Recargando, te aviso cuando vuelva.")
        # No se toca el mensaje: este listener esta a punto de morir y el
        # edit competiria con el apagado.
        return

    await query.answer(
        f"{fuente}: guardado en el .env. Se aplicara al reiniciar Scrappy.", show_alert=True
    )


async def _guardar(
    update: Update,
    editor: SourcesYamlEditor,
    fuente: str,
    clave: str,
    valores: list[str],
    app: ScrappyApp,
) -> bool:
    """Escribe la lista y la aplica en caliente. Devuelve si salio bien."""
    query = update.callback_query
    if not editor.set_value(["sources", fuente, clave], valores):
        texto = f"No hay seccion sources.{fuente}.{clave} en sources.yaml."
        if query is not None:
            await query.answer(texto, show_alert=True)
        return False

    try:
        # Asignarlo al app -y no solo guardarlo- es lo que lo aplica sin
        # reiniciar: el setter refresca el filtro y la config de los adapters.
        app.sources_config = editor.save()
    except ConfigError as exc:
        if query is not None:
            await query.answer(f"No se pudo guardar: {exc}"[:200], show_alert=True)
        return False
    return True


def _app_de(context: ContextTypes.DEFAULT_TYPE) -> ScrappyApp:
    app = context.bot_data.get("scrappy_app")
    if app is None:  # pragma: no cover - error de programacion
        raise RuntimeError("ScrappyApp no esta en bot_data")
    return app  # type: ignore[no-any-return]


async def despachar(
    update: Update, context: ContextTypes.DEFAULT_TYPE, accion: str, partes: list[str]
) -> None:
    """Reparte los botones de fuentes. Lo llama `callbacks.on_callback`.

    Valida la fuente antes de nada: los datos vienen de un mensaje, y aunque
    solo los pulsen administradores, un nombre inventado no debe llegar a
    construir una ruta dentro del YAML.
    """
    from scrappy.bot.keyboards import (
        ACCION_ANADIR,
        ACCION_COOKIES,
        ACCION_FUENTE,
        ACCION_FUENTES,
        ACCION_INTERRUPTOR,
        ACCION_INTERRUPTOR_OK,
        ACCION_ORIGENES,
        ACCION_QUITAR,
        ACCION_QUITAR_OK,
    )

    query = update.callback_query
    assert query is not None

    if accion == ACCION_FUENTES:
        await query.answer()
        await _pintar_menu(update, context)
        return

    fuente = partes[1] if len(partes) > 1 else ""
    if not fuente_conocida(fuente):
        await query.answer("No conozco esa fuente.", show_alert=True)
        return

    campo = _entero(partes, 2)
    necesita_campo = accion in (ACCION_ORIGENES, ACCION_ANADIR, ACCION_QUITAR, ACCION_QUITAR_OK)
    if necesita_campo and (campo is None or campo >= len(campos_de(fuente))):
        await query.answer("Ese campo ya no existe.", show_alert=True)
        return

    match accion:
        case _ if accion == ACCION_FUENTE:
            await query.answer()
            await _pintar_ficha(update, context, fuente)
        case _ if accion == ACCION_INTERRUPTOR:
            await _interruptor(update, context, fuente, encender=partes[2] == "1")
        case _ if accion == ACCION_INTERRUPTOR_OK:
            await _interruptor(update, context, fuente, encender=True, confirmado=True)
        case _ if accion == ACCION_COOKIES:
            await _renovar_cookies(update, context, fuente)
        case _ if accion == ACCION_ORIGENES:
            await query.answer()
            await _pintar_origenes(update, context, fuente, campo or 0)
        case _ if accion == ACCION_ANADIR:
            await _pedir_valor(update, context, fuente, campo or 0)
        case _ if accion == ACCION_QUITAR:
            await query.answer()
            await _confirmar_quitar(update, context, fuente, campo or 0, _entero(partes, 3) or 0)
        case _ if accion == ACCION_QUITAR_OK:
            await _quitar(update, context, fuente, campo or 0, _entero(partes, 3) or 0)


def _entero(partes: list[str], indice: int) -> int | None:
    """Un argumento numerico del `callback_data`, o None si no lo es."""
    if len(partes) <= indice or not partes[indice].isdigit():
        return None
    return int(partes[indice])


async def on_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recoge lo que se escribio en respuesta a un «anadir».

    Es el unico `MessageHandler` del bot, y por eso el filtro es tan estrecho:
    solo mira respuestas, y solo actua si la respuesta es **a la pregunta que
    este mismo usuario tiene pendiente**. Cualquier otra cosa escrita en el
    chat se ignora en silencio; un bot que contesta a mensajes que no van con
    el es insoportable en un grupo.

    Tampoco lleva `@admin_only` a proposito: el decorador responderia «No estas
    autorizado» a cualquiera que escriba, y quien no sea administrador nunca
    puede tener una pregunta pendiente -`user_data` es suyo- asi que sale por
    silencio, que es la respuesta correcta.
    """
    message = update.effective_message
    if message is None or message.text is None or message.reply_to_message is None:
        return

    pendientes: dict[int, tuple[str, int]] = (context.user_data or {}).get(CLAVE_PENDIENTES, {})
    encargo = pendientes.pop(message.reply_to_message.message_id, None)

    if encargo is None:
        # El aviso puede ser de antes de una recarga: `user_data` vive en el
        # Application, y la recarga estrena uno. Sin esto, el usuario escribe
        # y no pasa absolutamente nada, que es el peor final posible.
        texto_previo = message.reply_to_message.text or ""
        if texto_previo.startswith(PROMPT):
            await message.reply_text(
                "Ese aviso es de antes de recargar y ya no se a que se referia. "
                "Vuelve a pulsar «Anadir», por favor."
            )
        return

    fuente, campo = encargo
    clave, etiqueta = campos_de(fuente)[campo]
    app = _app_de(context)
    editor = _editor(app)
    valores = _valores(editor, fuente, clave)

    if valores is None:
        await message.reply_text(f"No hay seccion sources.{fuente}.{clave} en sources.yaml.")
        return

    anadidos, problemas = [], []
    for linea in message.text.replace(",", "\n").splitlines():
        if not linea.strip():
            continue
        try:
            valor = normalizar(clave, linea)
        except ValorNoValido as exc:
            problemas.append(f"«{linea.strip()}»: {exc}")
            continue
        if ya_esta(valores, valor):
            problemas.append(f"«{valor}»: ya estaba")
            continue
        if len(valores) >= MAXIMO_POR_LISTA:
            problemas.append(f"«{valor}»: la lista ya tiene {MAXIMO_POR_LISTA}")
            continue
        valores.append(valor)
        anadidos.append(valor)

    if anadidos and not await _guardar_en_mensaje(message, editor, fuente, clave, valores, app):
        return

    partes: list[str] = []
    if anadidos:
        log.info("origenes_anadidos", fuente=fuente, campo=clave, valores=anadidos)
        partes.append(f"Anadido a {fuente} · {etiqueta}: {', '.join(anadidos)}")
        partes.extend(await _sondear_cuentas_de_x(app, fuente, clave, anadidos))
    if problemas:
        partes.append("No se anadio:\n" + "\n".join(f"· {p}" for p in problemas))
    await message.reply_text("\n\n".join(partes) or "No habia nada que anadir.")


async def _sondear_cuentas_de_x(
    app: ScrappyApp, fuente: str, clave: str, cuentas: list[str]
) -> list[str]:
    """Comprueba si las cuentas de X recien anadidas traen video, y lo dice.

    **Avisa, no bloquea.** La cuenta ya esta guardada cuando esto corre: si el
    sondeo dice que no publica videos, es informacion para decidir, no un veto.
    Existe porque `@Memes` estuvo consultandose durante dias sin traer nada, y
    eso no se ve en ningun sitio hasta que uno mira el log.
    """
    if fuente != "x" or clave != "accounts" or not _tiene_sesion_renovable(fuente, app):
        return []

    from scrappy.sources.x import sondear_cuenta

    lineas = []
    for cuenta in cuentas[:5]:  # mas de cinco de golpe seria una rafaga
        try:
            lineas.append(await sondear_cuenta(app.settings, cuenta))
        except Exception as exc:  # pragma: no cover - el aviso no puede romper nada
            log.warning("sondeo_fallido", cuenta=cuenta, error=str(exc))
    return lineas


async def _guardar_en_mensaje(
    message: Any,
    editor: SourcesYamlEditor,
    fuente: str,
    clave: str,
    valores: list[str],
    app: ScrappyApp,
) -> bool:
    """Como `_guardar`, pero contestando a un mensaje en vez de a un boton."""
    if not editor.set_value(["sources", fuente, clave], valores):
        await message.reply_text(f"No hay seccion sources.{fuente}.{clave} en sources.yaml.")
        return False
    try:
        app.sources_config = editor.save()
    except ConfigError as exc:
        await message.reply_text(f"No se pudo guardar: {exc}"[:200])
        return False
    return True


__all__ = ["CLAVE_PENDIENTES", "PROMPT", "despachar", "on_respuesta"]
