"""Ajustes: todo lo configurable, en pestanas.

Cubre las dos capas de configuracion del proyecto:

- El **`.env`**, con los secretos y las decisiones de infraestructura.
- **`sources.yaml`**, con el catalogo y los pesos del ranking.

Los campos no se escriben a mano aqui: se declaran como datos en `fields.py` y
esta pantalla los pinta de forma generica. Anadir un ajuste a la interfaz es
una linea alli, no un widget nuevo aqui.

Tres cosas que conviene saber:

- **Los secretos se enmascaran.** El token no debe verse en pantalla; hay un
  interruptor para revelarlo cuando haga falta comprobarlo.
- **Se valida antes de escribir**, en los dos ficheros. Un `.env` o un YAML
  invalidos impiden arrancar, asi que ante un error te quedas con lo que tenias.
- **Los comentarios de ambos ficheros sobreviven.** Documentan por que cada
  valor es el que es, y perderlos seria peor que no poder editarlos.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import SecretStr
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from scrappy.core.errors import ConfigError
from scrappy.observability.logging import get_logger
from scrappy.tui.env_editor import EnvEditor, is_secret
from scrappy.tui.fields import (
    CONTENT_FIELDS,
    DELIVERY_FIELDS,
    FILTER_FIELDS,
    RANKING_FIELDS,
    SCHEDULE_FIELDS,
    SOURCE_ENABLED_KEY,
    STORAGE_FIELDS,
    TELEGRAM_FIELDS,
    TOS_RISKY,
    EnvField,
    FieldKind,
    YamlField,
    fields_for_source,
)
from scrappy.tui.yaml_editor import SourcesYamlEditor

if TYPE_CHECKING:
    from scrappy.tui.main import ScrappyTUI

log = get_logger(__name__)

_ENV_TABS: tuple[tuple[str, tuple[EnvField, ...]], ...] = (
    ("Telegram", TELEGRAM_FIELDS),
    ("Contenido", CONTENT_FIELDS),
    ("Programacion", SCHEDULE_FIELDS),
    ("Almacenamiento", STORAGE_FIELDS),
)

_YAML_TABS: tuple[tuple[str, tuple[YamlField, ...]], ...] = (
    ("Ranking", RANKING_FIELDS),
    ("Filtros", FILTER_FIELDS),
    ("Publicacion", DELIVERY_FIELDS),
)


class SettingsScreen(Screen[None]):
    """Editor de `.env` y `sources.yaml`."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+s", "guardar", "Guardar"),
        ("ctrl+r", "recargar", "Descartar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._env: EnvEditor | None = None
        self._yaml: SourcesYamlEditor | None = None
        #: Lo que se mostro al construir el formulario, por widget. Al guardar
        #: solo se escribe lo que difiera de esto.
        #:
        #: Sin ello, el volcado reescribia las ~40 claves en cada guardado, y
        #: una clave AUSENTE del `.env` -que el bot resolvia con su valor por
        #: defecto- acababa materializada con el valor equivocado. Asi se
        #: apagaron Lemmy y Bluesky en silencio.
        self._inicial: dict[str, str] = {}

    @property
    def tui(self) -> ScrappyTUI:
        return self.app  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Composicion
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="config-aviso", classes="ayuda")
        with Horizontal(id="acciones-scheduler"):
            yield Button("Guardar", id="guardar", variant="success")
            yield Button("Descartar cambios", id="recargar")
            yield Switch(id="revelar-secretos")
            yield Static("Mostrar secretos", classes="campo-etiqueta")
        yield TabbedContent(id="config-tabs")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        """Relee ambos ficheros y reconstruye el formulario."""
        scrappy = self.tui.scrappy
        if scrappy is None:
            return

        avisos: list[str] = []

        self._env = EnvEditor(self.tui.env_path)
        try:
            self._env.load()
        except ConfigError as exc:
            self._env = None
            avisos.append(str(exc))

        self._yaml = SourcesYamlEditor(scrappy.settings.sources_config_path)
        try:
            self._yaml.load()
        except ConfigError as exc:
            self._yaml = None
            avisos.append(str(exc))

        await self._construir()

        self.query_one("#config-aviso", Static).update(
            "\n".join(avisos)
            if avisos
            else "Los comentarios de ambos ficheros se conservan al guardar."
        )

    async def _construir(self) -> None:
        tabs = self.query_one("#config-tabs", TabbedContent)
        await tabs.clear_panes()
        # Se reconstruye el formulario entero, asi que los valores iniciales
        # anteriores ya no valen como referencia.
        self._inicial.clear()

        if self._env is not None:
            for titulo, campos_env in _ENV_TABS:
                await tabs.add_pane(self._panel_env(titulo, campos_env))

        if self._yaml is not None:
            for titulo, campos_yaml in _YAML_TABS:
                await tabs.add_pane(self._panel_yaml(titulo, campos_yaml))

            for fuente in self._yaml.source_names():
                await tabs.add_pane(self._panel_fuente(fuente))

    # ------------------------------------------------------------------
    # Construccion de paneles
    # ------------------------------------------------------------------
    def _panel_env(self, titulo: str, campos: tuple[EnvField, ...]) -> TabPane:
        return TabPane(
            titulo,
            VerticalScroll(*[self._campo_env(campo) for campo in campos]),
            id=f"tab-{_slug(titulo)}",
        )

    def _panel_yaml(self, titulo: str, campos: tuple[YamlField, ...]) -> TabPane:
        return TabPane(
            titulo,
            VerticalScroll(*[self._campo_yaml(campo) for campo in campos]),
            id=f"tab-{_slug(titulo)}",
        )

    def _panel_fuente(self, fuente: str) -> TabPane:
        hijos: list[Any] = []

        # El interruptor de activacion va aqui, junto a los ajustes de la
        # fuente, que es donde uno lo busca.
        clave = SOURCE_ENABLED_KEY.get(fuente)
        if clave and self._env is not None:
            hijos.append(
                self._campo_env(
                    EnvField(
                        clave,
                        f"Activar {fuente}",
                        _aviso_tos(fuente),
                        kind=FieldKind.BOOL,
                    )
                )
            )

        hijos += [self._campo_yaml(campo) for campo in fields_for_source(fuente)]
        return TabPane(fuente, VerticalScroll(*hijos), id=f"tab-src-{fuente}")

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------
    def _campo_env(self, campo: EnvField) -> Vertical:
        assert self._env is not None

        # Si la clave no esta en el `.env`, el bot usa el valor por defecto de
        # `Settings`. Mostrar una cadena vacia -que se pintaria como «apagado»-
        # seria mentir sobre lo que hace de verdad.
        valor = self._env.get_value(campo.key) or self._valor_efectivo(campo.key)

        self._inicial[campo.widget_id] = valor
        return self._envolver(campo.label, campo.help, self._widget(campo, valor))

    def _valor_efectivo(self, key: str) -> str:
        """Valor que usa el bot cuando la clave falta del fichero.

        Se lee de los `Settings` ya resueltos, que es la unica fuente que sabe
        lo que esta pasando de verdad.
        """
        scrappy = self.tui.scrappy
        if scrappy is None:
            return ""

        atributo = key.removeprefix("SCRAPPY_").lower()
        valor = getattr(scrappy.settings, atributo, None)
        if valor is None:
            return ""
        if isinstance(valor, bool):
            return "true" if valor else "false"
        if isinstance(valor, SecretStr):
            return valor.get_secret_value()
        return str(valor)

    def _campo_yaml(self, campo: YamlField) -> Vertical:
        assert self._yaml is not None
        valor = self._yaml.get_value(list(campo.path))
        self._inicial[campo.widget_id] = _como_texto(valor)
        return self._envolver(campo.label, campo.help, self._widget(campo, valor))

    @staticmethod
    def _envolver(etiqueta: str, ayuda: str, widget: Any) -> Vertical:
        hijos: list[Any] = [
            Horizontal(Static(etiqueta, classes="campo-etiqueta"), widget, classes="campo")
        ]
        if ayuda:
            hijos.append(Static(ayuda, classes="ayuda"))
        return Vertical(*hijos, classes="campo-bloque")

    def _widget(self, campo: EnvField | YamlField, valor: Any) -> Any:
        """Elige el widget segun el tipo declarado."""
        widget_id = campo.widget_id

        if campo.kind is FieldKind.BOOL:
            return Switch(value=_como_bool(valor), id=widget_id)

        if campo.kind is FieldKind.CHOICE and isinstance(campo, EnvField):
            opciones = [(opcion, opcion) for opcion in campo.choices]
            actual = str(valor) if str(valor) in campo.choices else campo.choices[0]
            return Select(opciones, value=actual, allow_blank=False, id=widget_id)

        if campo.kind is FieldKind.LIST:
            texto = ", ".join(str(item) for item in valor) if isinstance(valor, list) else ""
            return Input(value=texto, id=widget_id)

        # Los secretos se enmascaran; el interruptor de arriba los revela.
        oculto = isinstance(campo, EnvField) and is_secret(campo.key)
        return Input(value="" if valor is None else str(valor), password=oculto, id=widget_id)

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "revelar-secretos":
            self._revelar(event.value)

    def _revelar(self, mostrar: bool) -> None:
        for campo in TELEGRAM_FIELDS:
            if not is_secret(campo.key):
                continue
            try:
                self.query_one(f"#{campo.widget_id}", Input).password = not mostrar
            except Exception:  # la pestana puede no estar montada todavia
                continue

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "guardar":
            await self.action_guardar()
        elif event.button.id == "recargar":
            await self.action_recargar()

    async def action_recargar(self) -> None:
        await self.refresh_data()
        self.tui.set_status("Cambios descartados")

    async def action_guardar(self) -> None:
        """Vuelca el formulario y guarda los dos ficheros.

        Se guarda primero el YAML y despues el `.env`: si el segundo falla, lo
        peor que queda es una configuracion de catalogo aplicada a medias, y no
        un `.env` invalido que impida arrancar.
        """
        scrappy = self.tui.scrappy
        if scrappy is None:
            return

        try:
            self._volcar()
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return

        guardados: list[str] = []

        if self._yaml is not None and self._yaml.dirty:
            try:
                scrappy.sources_config = self._yaml.save()
                guardados.append("sources.yaml")
            except ConfigError as exc:
                self.notify(str(exc), severity="error")
                return

        if self._env is not None and self._env.dirty:
            try:
                self._env.save()
                guardados.append(".env")
            except ConfigError as exc:
                self.notify(str(exc), severity="error")
                return

        if not guardados:
            self.tui.set_status("No habia cambios que guardar")
            return

        self.tui.set_status(f"Guardado: {', '.join(guardados)}")

        if ".env" not in guardados:
            # El catalogo se relee en cada ronda; no hay nada que recargar.
            self.notify("Se aplica en la proxima ronda.")
            return

        # Los ajustes del `.env` se leen una sola vez, al construir la
        # aplicacion, asi que guardar no basta. Antes esto decia «reinicia
        # Scrappy»; ahora se recarga aqui mismo, que es lo que se queria hacer.
        if await self.tui.recargar():
            self.notify("Guardado y aplicado. No hace falta reiniciar.")
            await self.refresh_data()

    def _volcar(self) -> None:
        """Lleva lo escrito en los widgets a los editores.

        Raises:
            ValueError: algun campo numerico no lo es.
        """
        for entrada in self.query(Input):
            self._volcar_uno(entrada.id or "", entrada.value.strip())

        for interruptor in self.query(Switch):
            # El de revelar secretos es de la interfaz, no un ajuste.
            if interruptor.id != "revelar-secretos":
                self._volcar_uno(interruptor.id or "", "true" if interruptor.value else "false")

        for desplegable in self.query(Select):
            elegido = desplegable.value
            if elegido is not None:
                self._volcar_uno(desplegable.id or "", str(elegido))

    def _volcar_uno(self, widget_id: str, texto: str) -> None:
        # Solo se escribe lo que el usuario ha tocado. Reescribirlo todo es lo
        # que convertia una clave ausente en un valor explicito equivocado.
        if self._inicial.get(widget_id, _CENTINELA) == texto:
            return

        if widget_id.startswith("env-") and self._env is not None:
            self._env.set_value(widget_id.removeprefix("env-"), texto)
            return

        if widget_id.startswith("yaml-") and self._yaml is not None:
            ruta = widget_id.removeprefix("yaml-").split("__")
            self._yaml.set_value(ruta, _convertir(ruta, texto))

    # ------------------------------------------------------------------


#: Valor imposible: si un widget no esta en `_inicial`, se escribe siempre.
#: Una cadena vacia no serviria, porque es un valor legitimo.
_CENTINELA = "\x00no-registrado"


def _como_texto(valor: Any) -> str:
    """Representacion en texto de un valor del YAML, como la vera el widget."""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, list):
        return ", ".join(str(item) for item in valor)
    return "" if valor is None else str(valor)


def _slug(titulo: str) -> str:
    return titulo.lower().replace(" ", "-")


def _aviso_tos(fuente: str) -> str:
    if fuente in TOS_RISKY:
        return (
            "Incumple los terminos de su plataforma. Necesita ademas "
            "«Permitir fuentes con riesgo de ToS» en Almacenamiento. "
            "Lee docs/LEGAL.md."
        )
    if fuente == "x":
        return "El backend `api` necesita el tier de pago de X; el `scrape` incumple sus terminos."
    return ""


def _como_bool(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in {"true", "1", "yes", "si"}


#: Claves del YAML que son listas. El resto se interpreta por su contenido.
_CLAVES_LISTA = frozenset(
    {
        "subreddits",
        "communities",
        "queries",
        "accounts",
        "channels",
        "hashtags",
        "tags",
        "blocked_keywords",
        "blocked_authors",
        "allowed_languages",
    }
)

#: Claves del YAML que son booleanas.
_CLAVES_BOOL = frozenset({"show_score", "show_source_badge", "silent_notifications"})


def _convertir(ruta: list[str], texto: str) -> Any:
    """Convierte el texto del widget al tipo que espera el YAML."""
    hoja = ruta[-1]

    if hoja in _CLAVES_LISTA:
        return [parte.strip() for parte in texto.split(",") if parte.strip()]

    if hoja in _CLAVES_BOOL:
        return _como_bool(texto)

    # `instance`, `sort`, `rating`, `window` son texto libre.
    if hoja in {"instance", "sort", "rating", "window"}:
        return texto

    try:
        numero = float(texto)
    except ValueError as exc:
        raise ValueError(f"«{'.'.join(ruta)}» debe ser un numero, y pone «{texto}»") from exc
    return int(numero) if numero.is_integer() else numero
