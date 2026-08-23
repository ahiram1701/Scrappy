# Añadir una fuente

Una fuente nueva es **un fichero y una línea en el registro**. Nada más del pipeline necesita enterarse.

---

## El contrato

```python
class SourceAdapter(abc.ABC):
    name: str                    # el que aparece en sources.yaml y en los logs
    requires_tos_ack: bool       # ¿incumple los ToS de la plataforma?

    async def status(self) -> SourceStatus: ...
    async def discover(self, budget: int) -> list[RawCandidate]: ...
```

Un adapter **solo descubre**. No descarga, no filtra, no puntúa, no publica. Esa restricción es lo que mantiene cada plataforma aislada del resto.

### Reglas de comportamiento

1. **No lanzar por un item roto.** Sáltalo y sigue. Solo lanza `SourceError` si la fuente entera es inutilizable.
2. **Fallar pronto ante credenciales inválidas.** Autentica antes del bucle: reintentar con un token muerto es una ristra de peticiones condenadas y un camino rápido al rate limit.
3. **Rellenar siempre `permalink` y `author`.** Son la atribución, y la atribución es obligatoria.
4. **No falsear el User-Agent.** Scrappy se identifica honestamente.
5. **`requires_tos_ack = True` si la plataforma no ofrece API pública para este uso.** Es lo que mantiene la fuente desactivada por defecto.

---

## Caso A: la plataforma tiene API

Ejemplo de referencia: [`sources/reddit.py`](../src/scrappy/sources/reddit.py).

```python
class MiPlataformaSource(SourceAdapter):
    name = "miplataforma"
    requires_tos_ack = False

    async def status(self) -> SourceStatus:
        if not self.settings.miplataforma_token.get_secret_value():
            return SourceStatus(
                name=self.name,
                enabled=self.settings.miplataforma_enabled,
                configured=False,
                detail="falta SCRAPPY_MIPLATAFORMA_TOKEN",
            )
        return SourceStatus(name=self.name, enabled=True, configured=True)

    async def discover(self, budget: int) -> list[RawCandidate]:
        # Autentica ANTES del bucle.
        token = await self._token()

        candidates: list[RawCandidate] = []
        for tema in self.config.get_list("temas"):
            try:
                items = await self._buscar(tema, token, budget)
            except SourceError as exc:
                self.log.warning("tema_failed", tema=tema, error=str(exc))
                continue  # un tema roto no tumba la fuente
            candidates.extend(self._normalizar(item) for item in items)
        return candidates
```

**El `status()` importa más de lo que parece.** Es lo que ve el usuario en `/sources` y en `scrappy health` cuando algo no funciona. Un `detail` que diga exactamente qué variable falta ahorra media hora de depuración.

## Caso B: la plataforma no tiene API

Hereda de `YtDlpSource`, que ya resuelve la enumeración, las cookies y la normalización. Solo tienes que decir **qué URLs enumerar** y **cómo construir el permalink**:

```python
class MiPlataformaSource(YtDlpSource):
    name = "miplataforma"
    platform_label = "Mi Plataforma"
    cookies_required = True     # si no funciona sin sesión iniciada

    def collection_urls(self) -> list[str]:
        return [
            f"https://miplataforma.test/tag/{t.lstrip('#')}"
            for t in self.config.get_list("hashtags")
        ]

    def permalink_for(self, entry: dict[str, Any]) -> str:
        return entry.get("url") or f"https://miplataforma.test/v/{entry['id']}"
```

`requires_tos_ack = True` viene heredado, así que la fuente queda bloqueada salvo consentimiento explícito. Documenta la situación legal en [LEGAL.md](LEGAL.md).

---

## Caso C: la plataforma tiene una API, pero no para ti

Es el caso de X, y el más incómodo de los tres: hay una API interna —la que usa su propia web— a la que se puede llamar con las cookies de una sesión. Funciona, incumple los términos, y **se romperá**. Antes de escribir una fuente así, comprueba que no hay ninguna de las dos anteriores.

Lo que se aprendió montando [`x.py`](../src/scrappy/sources/x.py), que es lo que conviene repetir:

- **Descubrir y descargar pueden ir por caminos distintos.** yt-dlp no sabe enumerar timelines de X, pero sí descargar un tweet suelto. El pipeline ya separa las dos fases: basta con devolver `media_url=None` y un `permalink` que yt-dlp resuelva, y el descargador se encarga —cookies de la fuente incluidas—.
- **No fijes identificadores que la plataforma rota.** Los `queryId` de la GraphQL cambian sin avisar. Se leen del bundle JS de la propia web en cada arranque, con una salida de emergencia en el YAML por si el bundle cambia de forma. Hardcodearlos habría sido firmar que la fuente caduque en una fecha desconocida.
- **Filtra las cookies por dominio.** Un fichero de `--cookies-from-browser` trae el perfil entero del navegador. En el caso real venía con la sesión del correo de la cuenta. De un adapter solo debe salir lo que esa plataforma necesita.
- **Distingue «la sesión murió» de «esto falló».** Es el único error que el programa puede arreglar solo, y merece su propio tipo (`SesionInvalidaError`). Con eso, renovar y reintentar es un `except`, y el aviso a Telegram sale gratis.
- **Un User-Agent de navegador es una decisión, no un detalle.** Esa API responde 403 al User-Agent honesto del proyecto. Si tu fuente necesita disfrazarse, escríbelo en su docstring y en [LEGAL.md](LEGAL.md): que no lo descubra alguien leyendo el código dos años después.

## Normalizar bien

`RawCandidate` es donde la variedad de cada API se convierte en algo comparable.

```python
RawCandidate(
    source=self.name,
    source_id="id-estable-en-esa-plataforma",
    permalink="https://…",       # OBLIGATORIO: la atribución
    title="…",
    author="…",                  # OBLIGATORIO
    author_url="https://…",
    kind=MediaKind.VIDEO,        # VIDEO | ANIMATION | PHOTO
    media_url=None,              # None ⇒ se delega en yt-dlp
    thumbnail_url="https://…",
    duration_seconds=15.0,
    created_at=datetime(..., tzinfo=UTC),   # SIEMPRE con zona horaria
    engagement=5000,             # la métrica de popularidad más fiable
    comments=120,
    nsfw=False,
    language="es",
)
```

### Los tres campos con trampa

**`engagement`.** Elige la métrica más fiable de tu plataforma: upvotes en Reddit, likes+retweets en X, reproducciones en TikTok. **No la normalices tú**: el scorer la convierte a percentil dentro de su propio lote. Si intentas escalarla a mano romperás precisamente el mecanismo que hace comparables las plataformas ([RANKING.md](RANKING.md)).

**`created_at`.** Siempre con zona horaria. Las APIs mezclan naive y aware, y una resta entre ambos revienta. `RawCandidate` fuerza UTC en un validador, pero mejor que llegue bien.

**`media_url`.** Ponlo solo si es una URL directa y completa. Si el medio necesita resolverse (video DASH con audio en pista aparte, redirecciones, formatos múltiples) déjalo en `None` y yt-dlp se encarga. El video de Reddit es el ejemplo canónico: la URL directa existe, pero es **muda**.

---

## Registrarla

En [`sources/registry.py`](../src/scrappy/sources/registry.py):

```python
_FACTORIES: dict[str, AdapterFactory] = {
    "reddit": RedditSource,
    "x": build_x_source,
    "tiktok": TikTokSource,
    "instagram": InstagramSource,
    "miplataforma": MiPlataformaSource,   # ← aquí
}
```

Y añade sus flags en `config/settings.py`:

```python
miplataforma_enabled: bool = False
miplataforma_token: SecretStr = SecretStr("")
```

Si el nombre sigue el patrón `<fuente>_enabled`, el resto del sistema lo recoge solo.

---

## Probarla

Los tests de fuentes usan `respx` para interceptar httpx. Ninguno toca la red. Mira [`tests/unit/test_reddit_source.py`](../tests/unit/test_reddit_source.py) como plantilla.

```python
@respx.mock
async def test_normaliza_bien(settings, config):
    respx.get("https://miplataforma.test/api/buscar").mock(
        return_value=httpx.Response(200, json=PAYLOAD_REAL_RECORTADO)
    )
    async with httpx.AsyncClient() as client:
        candidates = await MiPlataformaSource(settings, config, client).discover(20)

    assert candidates[0].author == "pepita"
    assert candidates[0].permalink.startswith("https://")
```

**Usa payloads reales recortados**, no inventados. La mitad de los bugs de un adapter están en campos que la API devuelve de forma distinta a como imaginabas.

Cubre al menos:
- El camino feliz y la normalización de cada campo.
- Credenciales inválidas → error claro.
- Rate limit (429) → `RateLimitedError`.
- Un item roto en medio de una respuesta buena → se salta, no revienta.
- Contenido sin medio aprovechable → se descarta.

---

## Lista de comprobación

- [ ] `status()` dice exactamente qué falta cuando falta algo
- [ ] `permalink` y `author` siempre rellenos
- [ ] `created_at` con zona horaria
- [ ] Un item roto no tumba la fuente
- [ ] Credenciales inválidas fallan pronto y una sola vez
- [ ] `requires_tos_ack` refleja la realidad legal
- [ ] Registrada en `registry.py` y con sus flags en `settings.py`
- [ ] Documentada en `.env.example`, `sources.example.yaml`, `CONFIGURATION.md` y, si aplica, `LEGAL.md`
- [ ] Tests con `respx` cubriendo los cinco casos de arriba
- [ ] `make check` en verde
