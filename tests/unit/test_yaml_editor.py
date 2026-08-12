"""Pruebas del editor de `sources.yaml`.

La prueba que justifica toda la pieza es `test_los_comentarios_sobreviven`: los
comentarios del fichero son documentacion real, y PyYAML los borraria enteros.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrappy.core.errors import ConfigError
from scrappy.tui.yaml_editor import SourcesYamlEditor

YAML_CON_COMENTARIOS = """\
# =============================================================================
# Scrappy -- catalogo de fuentes
# =============================================================================
ranking:
  weights:
    engagement: 0.50   # cuanto pesa destacar dentro de su lote
    velocity: 0.35     # cuanto pesa hacerse viral rapido
    source: 0.15
  min_score: 0.35

sources:
  # ---------------------------------------------------------------------------
  # Reddit -- OJO CON EL RATE LIMIT: sin autenticar responde 429 con facilidad.
  # ---------------------------------------------------------------------------
  reddit:
    weight: 0.9
    budget: 60
    subreddits_per_run: 3
    subreddits:
      - memes
      - dankmemes

  lemmy:
    weight: 0.8
    budget: 50
    communities:
      - memes@lemmy.ml

filters:
  blocked_keywords:
    - gore
"""


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(YAML_CON_COMENTARIOS, encoding="utf-8")
    return path


@pytest.fixture
def editor(yaml_path: Path) -> SourcesYamlEditor:
    editor = SourcesYamlEditor(yaml_path)
    editor.load()
    return editor


# ---------------------------------------------------------------------------
# LA prueba
# ---------------------------------------------------------------------------
def test_los_comentarios_sobreviven(editor: SourcesYamlEditor, yaml_path: Path) -> None:
    """Es la razon de existir de ruamel aqui.

    Los comentarios explican por que cada valor es el que es. Con PyYAML se
    perderian en el primer guardado desde la interfaz.
    """
    editor.set_value(["ranking", "weights", "engagement"], 0.65)
    editor.save()

    guardado = yaml_path.read_text(encoding="utf-8")

    assert "# cuanto pesa destacar dentro de su lote" in guardado
    assert "# cuanto pesa hacerse viral rapido" in guardado
    assert "OJO CON EL RATE LIMIT" in guardado
    assert "Scrappy -- catalogo de fuentes" in guardado
    # Y el cambio se aplico.
    assert "engagement: 0.65" in guardado


def test_conserva_el_orden_de_las_claves(editor: SourcesYamlEditor, yaml_path: Path) -> None:
    """Un reordenado convertiria cualquier diff en ruido ilegible."""
    editor.set_value(["sources", "reddit", "budget"], 80)
    editor.save()

    guardado = yaml_path.read_text(encoding="utf-8")
    assert guardado.index("engagement:") < guardado.index("velocity:")
    assert guardado.index("reddit:") < guardado.index("lemmy:")


def test_no_toca_lo_que_no_se_le_pide(editor: SourcesYamlEditor, yaml_path: Path) -> None:
    original = yaml_path.read_text(encoding="utf-8")
    editor.set_value(["ranking", "min_score"], 0.5)
    editor.save()

    guardado = yaml_path.read_text(encoding="utf-8")
    # Solo cambia la linea de min_score.
    diferencias = [
        (a, b) for a, b in zip(original.splitlines(), guardado.splitlines(), strict=False) if a != b
    ]
    assert len(diferencias) == 1
    assert "min_score" in diferencias[0][1]


# ---------------------------------------------------------------------------
# Lectura y escritura
# ---------------------------------------------------------------------------
def test_lee_valores_anidados(editor: SourcesYamlEditor) -> None:
    assert editor.get_value(["ranking", "weights", "velocity"]) == 0.35
    assert editor.get_value(["sources", "reddit", "subreddits"]) == ["memes", "dankmemes"]


def test_devuelve_el_default_si_falta_la_ruta(editor: SourcesYamlEditor) -> None:
    assert editor.get_value(["sources", "inventada", "weight"], 0.5) == 0.5
    assert editor.get_value(["no", "existe"]) is None


def test_escribe_listas(editor: SourcesYamlEditor, yaml_path: Path) -> None:
    editor.set_value(["sources", "reddit", "subreddits"], ["memes", "funny", "aww"])
    editor.save()

    guardado = yaml_path.read_text(encoding="utf-8")
    assert "funny" in guardado and "aww" in guardado
    assert "OJO CON EL RATE LIMIT" in guardado


def test_no_crea_claves_que_no_existen(editor: SourcesYamlEditor) -> None:
    """Crear estructura desde la interfaz es justo lo que este editor no hace."""
    assert editor.set_value(["sources", "reddit", "clave_inventada"], 1) is False
    assert editor.set_value(["seccion", "que", "no", "existe"], 1) is False
    assert not editor.dirty


def test_marca_los_cambios_pendientes(editor: SourcesYamlEditor) -> None:
    assert not editor.dirty
    editor.set_value(["ranking", "min_score"], 0.4)
    assert editor.dirty
    editor.save()
    assert not editor.dirty


def test_asignar_el_mismo_valor_no_ensucia(editor: SourcesYamlEditor) -> None:
    assert editor.set_value(["ranking", "min_score"], 0.35) is True
    assert not editor.dirty


def test_lista_las_fuentes_en_su_orden(editor: SourcesYamlEditor) -> None:
    assert editor.source_names() == ["reddit", "lemmy"]


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def test_valida_antes_de_escribir(editor: SourcesYamlEditor, yaml_path: Path) -> None:
    """Si algo esta mal, mejor quedarse con lo que habia que con un fichero roto."""
    original = yaml_path.read_text(encoding="utf-8")
    # Un peso fuera de rango: el esquema lo limita a [0, 1].
    editor.set_value(["sources", "reddit", "weight"], 5.0)

    with pytest.raises(ConfigError, match="no es valida"):
        editor.save()

    assert yaml_path.read_text(encoding="utf-8") == original


def test_save_devuelve_la_config_ya_validada(editor: SourcesYamlEditor) -> None:
    editor.set_value(["ranking", "weights", "engagement"], 0.7)
    config = editor.save()
    assert config.ranking.weights.engagement == 0.7


# ---------------------------------------------------------------------------
# Errores de carga
# ---------------------------------------------------------------------------
def test_fichero_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="sources.example.yaml"):
        SourcesYamlEditor(tmp_path / "no-existe.yaml").load()


def test_yaml_invalido(tmp_path: Path) -> None:
    path = tmp_path / "roto.yaml"
    path.write_text("ranking: [sin cerrar", encoding="utf-8")

    with pytest.raises(ConfigError, match="no es YAML valido"):
        SourcesYamlEditor(path).load()


def test_fichero_vacio(tmp_path: Path) -> None:
    path = tmp_path / "vacio.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="vacio"):
        SourcesYamlEditor(path).load()


def test_usar_sin_cargar(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="load"):
        SourcesYamlEditor(tmp_path / "x.yaml").get_value(["a"])


def test_el_ejemplo_real_del_proyecto_sobrevive_al_round_trip(tmp_path: Path) -> None:
    """Prueba contra el fichero de verdad, no contra uno de laboratorio."""
    real = Path("config/sources.example.yaml")
    if not real.exists():  # pragma: no cover - solo si se ejecuta fuera del repo
        pytest.skip("no se encuentra config/sources.example.yaml")

    copia = tmp_path / "sources.yaml"
    copia.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")

    editor = SourcesYamlEditor(copia)
    editor.load()
    editor.set_value(["ranking", "min_score"], 0.42)
    editor.save()

    guardado = copia.read_text(encoding="utf-8")
    assert "min_score: 0.42" in guardado
    # Comentarios reales del fichero, de los que costo descubrir.
    assert "OJO CON EL RATE LIMIT" in guardado
    assert "window_hours" in guardado
    assert "no expone" in guardado.lower()
