# ADR-0006: Deduplicación perceptual con pHash de frames

**Estado:** aceptada · **Fecha:** 2026-08-11

## Contexto

El mismo meme circula por todas las plataformas a la vez, y nunca llega igual: recomprimido, reescalado, con una marca de agua distinta o recortado. Un canal que no lo detecte acaba publicando el mismo clip tres veces en una tarde, que es justo lo que arruina la sensación de que hay alguien curando.

Comparar por URL no sirve. Comparar por hash del fichero tampoco: dos recompresiones del mismo video dan `sha256` completamente distintos.

## Decisión

**Tres puertas, en orden de coste creciente:**

| # | Comprobación | Coste | Cuándo |
|---|---|---|---|
| 1 | `uid` = `source:source_id` | una consulta | **antes de descargar** |
| 2 | `sha256` del fichero | el hash | tras descargar |
| 3 | pHash perceptual, distancia de Hamming ≤ umbral | ffmpeg + DCT | tras descargar |

El orden es lo importante: la primera puerta ahorra la descarga entera, que es el paso caro.

**Para video**, el pHash se calcula sobre **tres frames** muestreados al 25 %, 50 % y 75 % de la duración, y se concatenan los tres hashes de 64 bits.

- Se evitan el 0 % y el 100 % porque muchísimos clips empiezan o acaban en negro, y eso haría que videos distintos compartieran hash.
- Se **concatenan** en vez de promediarse para que la distancia siga siendo interpretable: se suman las distancias bloque a bloque. Un clip que coincide en dos de tres frames sigue siendo el mismo clip con otro corte.
- Los frames salen de ffmpeg **por stdout** (`-f image2pipe`), así que ni siquiera el frame llega a ser un fichero. Coherente con [ADR-0008](0008-almacenamiento-efimero.md).

**Umbral por defecto: 6** sobre 64 bits, configurable con `SCRAPPY_PHASH_THRESHOLD`.

**Ventana de 90 días** para los hashes con los que se compara: un repost de hace un año ya no molesta a nadie, y comparar contra todo el histórico crece sin límite.

## Alternativas descartadas

**Solo `sha256`.** No detecta nada de lo que importa. Cualquier recompresión lo esquiva.

**Un frame único.** Frágil: basta con que el repost tenga un corte de medio segundo al principio para que el hash cambie por completo.

**`videohash` u otras librerías especializadas.** Más precisas, pero arrastran dependencias pesadas para un problema que tres frames y `imagehash` resuelven razonablemente.

**Hash de audio (huella acústica).** Sería complementario y bueno, pero muchos memes son mudos o llevan audio genérico, y añade otra dependencia nativa.

**Comparación por embeddings de un modelo de visión.** Mucho más preciso, y desproporcionado: un modelo cargado en memoria permanentemente para evitar reposts en un bot de memes.

## Consecuencias

- **La puerta 3 no es gratis:** compara contra todos los hashes de la ventana. Con miles de items publicados la comparación lineal empieza a notarse; si eso ocurre, la solución es un índice BK-tree, no bajar la ventana.
- **El umbral es un compromiso.** A 6 se escapan algunos reposts muy transformados; a 10 empieza a marcar como duplicadas cosas que solo se parecen. Es configurable precisamente porque el punto óptimo depende del tipo de contenido.
- **Los hashes de longitud distinta no se comparan** (un video con tres frames frente a uno con uno): se devuelve una distancia imposible de superar el umbral, para no producir falsos positivos.
- Los tests usan imágenes con **estructura de mediana frecuencia**, no degradados suaves. El pHash decide cada bit comparando un coeficiente de la DCT contra la mediana; en una imagen casi plana todos los coeficientes están pegados a esa mediana y el ruido de la recompresión los hace oscilar en masa. Eso daría falsos negativos que no le ocurren a ningún meme real.
