## Que cambia

<!-- Una o dos frases. Si hay una issue, enlazala: Closes #123 -->

## Por que

<!-- El motivo, no la implementacion. Es lo que se pierde al leer el diff dentro de seis meses. -->

## Como probarlo

<!-- Pasos concretos. Si toca una fuente, di si lo probaste contra la plataforma real. -->

---

### Comprobaciones

- [ ] `make check` pasa en local (ruff, mypy y tests)
- [ ] He anadido o actualizado tests
- [ ] He actualizado la documentacion afectada
- [ ] `.env.example` sigue cubriendo todas las variables si he tocado `settings.py`

### Si el cambio toca la descarga o el ciclo de vida de los medios

- [ ] Todo fichero temporal sigue naciendo dentro de un `EphemeralWorkspace`
- [ ] Los tests de `tests/integration/test_pipeline.py` que verifican que no
      queda ningun fichero siguen pasando, tanto en el camino feliz como en el
      de fallo

### Si el cambio anade una fuente

- [ ] He indicado si la plataforma ofrece API publica para este uso
- [ ] Si no la ofrece, `requires_tos_ack = True` y esta documentado en `docs/LEGAL.md`
- [ ] La atribucion al autor original funciona (`permalink` y `author` correctos)
