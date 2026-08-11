"""Pruebas de deduplicacion.

La dedup perceptual es la que evita que el mismo meme aparezca tres veces
recomprimido desde tres plataformas distintas, asi que se comprueba tanto que
detecte lo parecido como que NO junte cosas distintas.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from scrappy.core.errors import DuplicateItemError
from scrappy.storage.backends import MemoryStateBackend
from scrappy.storage.dedup import (
    Deduplicator,
    hamming_distance,
    phash_of_image_bytes,
    sha256_of_bytes,
    sha256_of_file,
)
from tests.conftest import make_candidate, make_media


def _imagen(color: tuple[int, int, int], size: int = 64, quality: int = 95) -> bytes:
    """Genera un JPEG solido del color pedido."""
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


# Patron de 8x8 bloques con contraste fuerte, elegido a mano y fijo. Imita la
# estructura de un meme real: regiones grandes y bien definidas.
_PATRON = (
    (240, 20, 200, 40, 230, 60, 190, 10),
    (30, 210, 50, 180, 20, 240, 70, 200),
    (220, 40, 250, 10, 200, 30, 240, 60),
    (10, 190, 20, 230, 50, 210, 30, 180),
    (250, 60, 210, 30, 240, 20, 220, 40),
    (40, 230, 70, 200, 10, 190, 50, 250),
    (200, 10, 240, 60, 220, 40, 180, 20),
    (60, 250, 30, 220, 40, 230, 10, 210),
)


def _degradado(shift: int = 0, size: int = 128) -> bytes:
    """Imagen de prueba con estructura de mediana frecuencia.

    La estructura importa mucho aqui. El pHash decide cada bit comparando un
    coeficiente de la DCT contra la mediana, asi que una imagen casi plana
    —un degradado suave o un color solido— tiene todos los coeficientes altos
    pegados a esa mediana, y el ruido de la recompresion los hace oscilar en
    masa. El resultado serian falsos negativos que no le ocurren a ningun meme
    real. Con bloques contrastados los coeficientes quedan lejos de la mediana
    y el hash se comporta como se comporta con contenido de verdad.

    `shift` rota el patron para producir una imagen genuinamente distinta.
    """
    block = size // 8
    image = Image.new("RGB", (size, size))
    for bx in range(8):
        for by in range(8):
            value = _PATRON[(by + shift) % 8][(bx + shift) % 8]
            for x in range(bx * block, (bx + 1) * block):
                for y in range(by * block, (by + 1) * block):
                    image.putpixel((x, y), (value, 255 - value, (value * 2) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------
def test_sha256_de_bytes_y_de_fichero_coinciden(tmp_path: Path) -> None:
    data = b"mismo contenido"
    fichero = tmp_path / "x.bin"
    fichero.write_bytes(data)
    assert sha256_of_bytes(data) == sha256_of_file(fichero)


def test_phash_de_imagenes_identicas_es_igual() -> None:
    imagen = _degradado()
    assert phash_of_image_bytes(imagen) == phash_of_image_bytes(imagen)


def test_phash_sobrevive_a_la_recompresion() -> None:
    """El caso real: el mismo meme reencodado por otra plataforma.

    Es exactamente lo que ocurre cuando un clip de Reddit reaparece en TikTok:
    mismo contenido, otra compresion, otro sha256.
    """
    original = Image.open(io.BytesIO(_degradado()))

    alta = io.BytesIO()
    original.save(alta, format="JPEG", quality=95)
    baja = io.BytesIO()
    original.save(baja, format="JPEG", quality=50)

    h1 = phash_of_image_bytes(alta.getvalue())
    h2 = phash_of_image_bytes(baja.getvalue())
    assert h1 is not None and h2 is not None
    assert hamming_distance(h1, h2) <= 6


def test_phash_sobrevive_al_reescalado() -> None:
    """Republicar suele implicar reescalar: el hash tiene que aguantarlo."""
    original = Image.open(io.BytesIO(_degradado(size=256)))

    grande = io.BytesIO()
    original.save(grande, format="JPEG", quality=90)
    pequena = io.BytesIO()
    original.resize((128, 128)).save(pequena, format="JPEG", quality=90)

    h1 = phash_of_image_bytes(grande.getvalue())
    h2 = phash_of_image_bytes(pequena.getvalue())
    assert h1 is not None and h2 is not None
    assert hamming_distance(h1, h2) <= 6


def test_phash_de_bytes_invalidos_no_revienta() -> None:
    assert phash_of_image_bytes(b"esto no es una imagen") is None


def test_hamming_de_hashes_incomparables() -> None:
    """Longitudes distintas = no comparables; nunca deben parecer duplicados."""
    assert hamming_distance("abcd", "abcdef") > 64
    assert hamming_distance("zzz", "zzz") > 64  # no es hexadecimal valido


def test_hamming_de_hashes_concatenados() -> None:
    """Los videos usan 3 bloques de 64 bits concatenados."""
    bloque = phash_of_image_bytes(_degradado())
    assert bloque is not None
    triple = bloque * 3
    assert hamming_distance(triple, triple) == 0


# ---------------------------------------------------------------------------
# Deduplicator
# ---------------------------------------------------------------------------
async def test_puerta_1_detecta_el_uid_ya_publicado(state: MemoryStateBackend) -> None:
    from scrappy.core.models import PublishedItem

    await state.record(
        PublishedItem(source="reddit", source_id="abc123", permalink="x", sha256="a" * 64)
    )
    dedup = Deduplicator(state)
    assert await dedup.is_known_uid("reddit:abc123")
    assert not await dedup.is_known_uid("reddit:otro")


async def test_puerta_2_detecta_el_mismo_fichero(state: MemoryStateBackend) -> None:
    from scrappy.core.models import PublishedItem

    digest = "b" * 64
    await state.record(PublishedItem(source="x", source_id="1", permalink="x", sha256=digest))

    dedup = Deduplicator(state)
    with pytest.raises(DuplicateItemError, match="sha256"):
        await dedup.assert_not_duplicate(make_media(sha256=digest))


async def test_puerta_3_detecta_un_repost_recomprimido(
    state: MemoryStateBackend,
) -> None:
    from scrappy.core.models import PublishedItem

    hash_original = phash_of_image_bytes(_degradado())
    assert hash_original is not None

    await state.record(
        PublishedItem(
            source="reddit",
            source_id="original",
            permalink="x",
            sha256="c" * 64,
            phash=hash_original,
        )
    )

    # Mismo contenido, otra plataforma, otro fichero: sha256 distinto.
    dedup = Deduplicator(state, phash_threshold=6)
    repost = make_media(
        candidate=make_candidate(source="tiktok", source_id="repost"),
        sha256="d" * 64,
        phash=hash_original,
    )

    with pytest.raises(DuplicateItemError, match="phash"):
        await dedup.assert_not_duplicate(repost)


async def test_no_marca_como_duplicado_lo_que_es_distinto(
    state: MemoryStateBackend,
) -> None:
    from scrappy.core.models import PublishedItem

    primero = phash_of_image_bytes(_imagen((250, 10, 10)))
    segundo = phash_of_image_bytes(_degradado(shift=128))
    assert primero is not None and segundo is not None

    await state.record(
        PublishedItem(source="reddit", source_id="1", permalink="x", sha256="e" * 64, phash=primero)
    )

    dedup = Deduplicator(state, phash_threshold=6)
    await dedup.assert_not_duplicate(make_media(sha256="f" * 64, phash=segundo))


async def test_sin_phash_no_se_compara(state: MemoryStateBackend) -> None:
    """Si no se pudo calcular el hash perceptual, se publica igualmente."""
    dedup = Deduplicator(state)
    await dedup.assert_not_duplicate(make_media(sha256="0" * 64, phash=None))


async def test_compute_hashes_rellena_el_medio_en_memoria(
    state: MemoryStateBackend,
) -> None:
    imagen = _degradado()
    media = make_media(data=imagen, sha256="")
    media.data = imagen
    media.size_bytes = len(imagen)

    resultado = await Deduplicator(state).compute_hashes(media)

    assert resultado.sha256 == sha256_of_bytes(imagen)
    assert resultado.phash is not None
