# ADR-0007: Las fuentes que incumplen ToS vienen desactivadas

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

De las cuatro fuentes pedidas, solo una —Reddit— ofrece API pública para este caso de uso. X en modo scrape, TikTok e Instagram requieren enumerar contenido de formas que **incumplen los términos de servicio** de esas plataformas.

Eso plantea una pregunta de diseño que no es técnica: ¿qué hace el proyecto al respecto?

Las posturas posibles van desde "no implementarlas" hasta "implementarlas sin más y que cada cual se apañe". Ninguna de las dos me parece correcta: la primera ignora una petición explícita del usuario, la segunda le hace tomar una decisión con consecuencias sin darle la información para tomarla.

## Decisión

**Implementarlas por completo, pero desactivadas por defecto y detrás de un consentimiento explícito.**

Cinco mecanismos:

1. **`requires_tos_ack = True`** en la clase del adapter. La situación legal está codificada en el tipo, no en un comentario que nadie lee.
2. **Un interruptor maestro**, `SCRAPPY_ENABLE_TOS_RISKY_SOURCES`, a `false`. Sin él en `true`, esas fuentes no arrancan aunque estén habilitadas individualmente.
3. **Un aviso en el arranque** cuando el flag está activo.
4. **Documentación honesta** en [LEGAL.md](../LEGAL.md): qué se incumple exactamente, qué riesgo real hay, y cómo escala ese riesgo con el alcance del canal.
5. **Atribución obligatoria y no desactivable.** No existe opción de configuración para quitarla, en ninguna fuente.

Y algo importante sobre el comportamiento: una fuente bloqueada por falta de consentimiento **no revienta el arranque**. Se omite con un aviso. Tener `tiktok_enabled=true` sin el flag no puede impedir que Reddit siga publicando.

## Alternativas descartadas

**No implementarlas.** Es la opción más cómoda para el proyecto, pero el usuario las pidió explícitamente después de que se le explicara el problema. Negarse a construir lo que alguien decide construir para sí mismo, tras informarle, es paternalismo, no responsabilidad.

**Implementarlas sin ninguna barrera.** Convertiría el incumplimiento en el camino por defecto, y alguien que solo quería un bot de memes acabaría con la cuenta de Instagram bloqueada sin haber entendido nunca por qué.

**Un diálogo interactivo de aceptación al arrancar.** Imposible en un contenedor sin TTY, y acabaría en un `--yes` en el `CMD` que nadie lee. Una variable de entorno que hay que escribir a mano cumple la misma función y sobrevive al despliegue automatizado.

**Ocultarlas tras un *plugin* externo.** Deja el código principal "limpio" pero es un lavado de manos: el proyecto seguiría siendo quien lo hace posible, solo que con más pasos.

## Consecuencias

- Quien quiera esas fuentes tiene que hacer algo deliberado y leer por qué. Es exactamente el objetivo.
- El código tiene una ramificación extra (`ensure_tos_acknowledged`) que hay que respetar en todo adapter nuevo. Está en la lista de comprobación de [SOURCES.md](../SOURCES.md) y en la plantilla de PR.
- El proyecto asume una postura explícita en vez de esconderse. Creo que es lo correcto, y además hace el README más útil: la sección "antes de empezar, lee esto" dice de verdad algo.
- Si alguna de esas plataformas abriera una API pública para este uso, su adapter pasaría a `requires_tos_ack = False` y dejaría de estar tras el flag. El diseño ya lo contempla.
