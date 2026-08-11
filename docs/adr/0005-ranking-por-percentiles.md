# ADR-0005: Ranking por percentiles, no por métricas brutas

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

Hay que ordenar candidatos que vienen de plataformas cuyas métricas no son comparables:

| Plataforma | Métrica | Orden de magnitud típico de un buen post |
|---|---|---|
| Reddit | upvotes | 10.000 – 50.000 |
| X | likes + retweets | 2.000 – 30.000 |
| TikTok | reproducciones | 100.000 – 5.000.000 |
| Instagram | likes | 5.000 – 200.000 |

Comparar esos números directamente significa que TikTok gana **siempre**, no por ser mejor sino porque su unidad de medida es mayor. Y dentro de una misma plataforma, un post de hace dos días acumula más que uno de hace dos horas aunque el segundo esté explotando y el primero ya esté muerto.

## Decisión

**Nunca comparar métricas brutas entre fuentes.** Cada candidato se convierte al **percentil que ocupa dentro de su propia fuente y su propio lote**.

```
score = w_engagement · engagement_norm     percentil en su lote  [0,1]
      + w_velocity   · velocity_norm       log1p(eng)/log1p(edad+2), normalizado al p90
      + w_source     · source_weight       preferencia manual     [0,1]
      − penalizaciones                     restas, no multiplicadores
```

Cuatro decisiones dentro de la fórmula que merecen justificación:

**`log1p` en la velocidad.** El engagement tiene cola larguísima. Sin comprimirlo, un único post con un millón de likes dejaría a todos los demás indistinguibles de cero.

**`+2` en la edad.** Evita la división por cero y la velocidad infinita de un post de cero horas.

**Normalizar la velocidad contra el percentil 90, no contra el máximo.** Con el máximo, un solo item viral aplastaría a todo el resto a valores cercanos a cero.

**Penalizaciones que restan en vez de multiplicar.** Si `too_long` vale 0.20, un clip largo pierde exactamente 0.20 puntos. Con un multiplicador habría que hacer cuentas para saber qué pasó. La legibilidad importa porque estos valores están pensados para que el usuario los ajuste.

Todo —pesos, penalizaciones, umbrales— vive en `config/sources.yaml`, no en el código.

## Alternativas descartadas

**Normalizar por el máximo del lote** (`valor / max`). Simple, pero un outlier viral comprime a todos los demás contra cero y el ranking pierde capacidad de discriminar entre los items normales.

**Z-score.** Estadísticamente más fino, pero el engagement no se distribuye normalmente ni de lejos —es una ley de potencias—, así que el z-score da valores engañosos. El percentil no asume ninguna distribución.

**Escalas fijas por plataforma** (dividir TikTok entre 20, etc.). Frágil y arbitrario: las escalas cambian con el tiempo y con el nicho, y habría que recalibrarlas a mano cada pocos meses.

**Un modelo de ML.** Sin datos etiquetados no hay nada que entrenar, y "gracioso" no es una etiqueta que se pueda inferir del engagement. Sería complejidad sin señal.

## Consecuencias

- **El mismo post puede sacar distinta nota en dos ejecuciones**, porque compite contra otros. Es deliberado: se busca lo mejor *de hoy*, no una nota absoluta.
- **El tamaño del lote importa.** Con 5 candidatos los percentiles son muy gruesos. Por eso `budget` está a 60 en Reddit y subirlo suele ser mejor palanca que tocar los pesos.
- **Hace falta una forma de calibrar sin publicar**, y de ahí viene `--dry-run` con la tabla de desglose. Sin ella, ajustar los pesos sería adivinar.
- El `ScoreBreakdown` se conserva en el modelo precisamente para poder explicar cada nota.

Explicación completa y recetas de ajuste en [RANKING.md](../RANKING.md).
