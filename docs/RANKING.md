# Cómo se decide qué es "lo mejor"

Este documento explica la fórmula de ranking, por qué es así, y cómo ajustarla a tu gusto sin tocar código.

---

## El problema

Si le pides a un bot "publica lo mejor de internet", lo primero que hay que resolver es una trampa que no es obvia:

> **12.000 upvotes de Reddit no son 12.000 likes de TikTok.**

Las escalas son incomparables. Un post excelente de r/memes ronda los 30.000 upvotes; un TikTok mediocre pasa de 200.000 reproducciones sin despeinarse. Un bot que compare esos números en bruto publicará **siempre TikTok**, no porque sea mejor, sino porque su unidad de medida es más grande.

Lo mismo pasa dentro de una misma plataforma en el eje del tiempo: un post de hace dos días acumula más que uno de hace dos horas, aunque el segundo esté explotando ahora mismo y el primero ya esté muerto.

---

## La solución

Nunca se comparan números brutos. Cada candidato se convierte primero al **percentil que ocupa dentro de su propio lote y su propia fuente**.

"Está en el 10 % mejor de lo que trajo Reddit hoy" y "está en el 10 % mejor de lo que trajo TikTok hoy" **sí** significan lo mismo, y eso sí se puede comparar.

```
score = w_engagement · engagement_norm     ← percentil dentro de su lote
      + w_velocity   · velocity_norm       ← popularidad por hora de vida
      + w_source     · source_weight       ← tu sesgo manual por plataforma
      − penalizaciones
```

Con los pesos por defecto (0.50 / 0.35 / 0.15) el score cae en `[0, 1]`.

---

## Los tres términos

### `engagement_norm` — ¿destaca entre los suyos?

El percentil del engagement bruto dentro de los candidatos de la misma fuente en esta ejecución. El mejor de su lote saca 1.0, el peor 0.0, los empates comparten percentil.

Si una fuente solo trajo un item, saca 1.0: es lo mejor que trajo esa fuente.

**Consecuencia importante:** el mismo post puede sacar distinta nota en dos ejecuciones, porque compite contra otros. Es deliberado. Lo que se busca es lo mejor **de hoy**, no una nota absoluta.

### `velocity_norm` — ¿está explotando ahora?

```
velocity = log1p(engagement) / log1p(edad_horas + 2)
```

Este es el término que detecta lo que se está haciendo viral **en este momento**. Un clip con 5.000 likes en dos horas puntúa más alto que uno con 20.000 en dos días.

Tres detalles del diseño:

- **`log1p` en el numerador** porque el engagement tiene cola larguísima. Sin comprimirlo, un único post con un millón de likes dejaría a todos los demás indistinguibles de cero.
- **`+2` en la edad** para que un post de cero horas no produzca una división por cero ni una velocidad infinita.
- **Se normaliza contra el percentil 90 del lote**, no contra el máximo. Usar el máximo haría que un solo item viral aplastara a todos los demás.

### `source_weight` — tu preferencia

Un valor fijo por plataforma que fijas tú en `sources.yaml`. Si Reddit te da mejor material que TikTok, súbele el peso. Es el término menos "inteligente" y el más honesto: reconoce que tú tienes criterio sobre de dónde sale lo que te gusta.

---

## Penalizaciones

Se **restan** del score en vez de multiplicarlo, para que su efecto sea legible: si `too_long` vale 0.20, un clip largo pierde exactamente 0.20 puntos. Con un multiplicador tendrías que hacer cuentas para saber qué pasó.

| Penalización | Cuándo | Por defecto |
|---|---|---|
| `too_long` | Supera `SCRAPPY_MAX_DURATION_SECONDS` | 0.20 |
| `too_short` | Baja de `SCRAPPY_MIN_DURATION_SECONDS` | 0.10 |
| `no_thumbnail` | No hay miniatura | 0.05 |
| `low_comment_ratio` | Muchos likes y casi ningún comentario | 0.10 |

La última merece explicación. Un ratio comentarios/engagement muy bajo suele indicar engagement inflado o contenido que no genera ninguna conversación. Pero **solo se evalúa a partir de 1.000 de engagement**: en un post con 50 likes, cero comentarios no significa absolutamente nada.

El score nunca baja de cero, por muchas penalizaciones que acumule.

---

## Los cortes

Después de puntuar hay dos filtros:

1. **`ranking.min_score`** (0.35 por defecto). Por debajo de eso no se descarga ni se publica.
2. **El límite de items del run** (`SCRAPPY_ITEMS_PER_RUN`, o el `-n` de la CLI).

El pipeline selecciona **el triple** de items que va a publicar, porque algunos se caerán al descargar: posts borrados, privados, o que no caben en el límite de tamaño.

---

## Cómo calibrarlo

La herramienta para esto es el ensayo en seco. **No descarga ni publica nada:**

```bash
scrappy fetch --dry-run
```

```
                 Candidatos evaluados (no se descargo nada)
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ score ┃ fuente ┃ veredicto    ┃ engagement ┃ titulo                     ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 0.891 │ reddit │ SELECCIONADO │     48,213 │ He didn't expect that      │
│ 0.774 │ reddit │ SELECCIONADO │     22,904 │ Perfectly timed            │
│ 0.301 │ reddit │ nota baja    │      1,204 │ Meh                        │
│ 0.000 │ reddit │ demasiado…   │        890 │ 4 hour compilation         │
└───────┴────────┴──────────────┴────────────┴────────────────────────────┘
```

Ejecuta esto unas cuantas veces a distintas horas antes de tocar nada. Los pesos por defecto están razonablemente calibrados para memes; el problema casi nunca son los pesos, es el catálogo de subreddits.

### Recetas concretas

| Lo que te pasa | Qué tocar |
|---|---|
| Publica cosas demasiado mainstream y previsibles | Sube `velocity` a 0.50 y baja `engagement` a 0.35 |
| Publica cosas raras con poco engagement | Al revés: `engagement` 0.65, `velocity` 0.20 |
| Publica poco, o nada | Baja `min_score` a 0.25, o sube el `budget` de las fuentes |
| Publica demasiada morralla | Sube `min_score` a 0.50 |
| Una fuente domina el canal | Baja su `weight`, o baja su `budget` |
| Se cuelan clips larguísimos | Sube `too_long` a 0.40 y baja `SCRAPPY_MAX_DURATION_SECONDS` |
| Quieres ver los scores en el canal | `delivery.show_score: true` |

Un consejo: **cambia una cosa cada vez** y vuelve a mirar el `--dry-run`. Tocar tres parámetros a la vez y quedarte con el resultado es adivinar, no calibrar.

---

## Configuración completa

```yaml
ranking:
  weights:
    engagement: 0.50
    velocity: 0.35
    source: 0.15

  penalties:
    too_long: 0.20
    too_short: 0.10
    no_thumbnail: 0.05
    low_comment_ratio: 0.10

  min_comment_ratio: 0.002
  min_score: 0.35

sources:
  reddit:
    weight: 0.9      # ← source_weight
    budget: 60       # ← candidatos a pedir antes de filtrar
```

**Sobre `budget`:** es cuántos candidatos pide cada fuente *antes* de filtrar y rankear. Pedir de más cuesta muy poco —solo son metadatos, una o dos peticiones— y mejora bastante la selección final, porque el percentil se calcula sobre una muestra mayor. Subirlo suele ser mejor idea que tocar los pesos.

---

## Lo que esta fórmula no hace

Por honestidad sobre sus límites:

- **No entiende el contenido.** No sabe si un video es gracioso. Solo sabe que a mucha gente le pareció que sí, rápido.
- **Hereda los sesgos de las plataformas.** Si un algoritmo de recomendación empuja cierto tipo de contenido, aquí se refleja.
- **No detecta engagement comprado** más allá de la heurística tosca del ratio de comentarios.
- **Depende por completo del catálogo.** Con subreddits mediocres, el mejor ranking del mundo elige lo mejor de lo mediocre.

Ese último punto es el más importante en la práctica: **la calidad de tu canal la determinan sobre todo las fuentes que elijas**, no los pesos.
