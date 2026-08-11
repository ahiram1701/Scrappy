# La garantía de "nada en disco"

Scrappy descarga contenido para publicarlo en Telegram y luego borrarlo. El contenido **no se archiva en la máquina**. Este documento explica cómo se cumple eso, qué es exactamente lo que sí persiste, y cómo comprobarlo tú mismo.

---

## La promesa, con precisión

> Ningún medio (video, imagen o GIF) descargado por Scrappy permanece en la máquina después de que su ciclo termine. Telegram es el único archivo.

Y lo que la promesa **no** dice, para que no haya malentendidos:

- Sí se guardan ~200 bytes de **metadatos** por item publicado (hashes e identificadores), para no repetir contenido. Se puede desactivar.
- El contenido **sí** queda en Telegram, obviamente, que es el objetivo.
- Un `kill -9` en el instante exacto de una descarga puede dejar un fichero hasta el siguiente arranque, que lo barre. Contra `SIGKILL` no hay defensa posible en ningún programa.

---

## Cómo se cumple

Cumplir esto no es cuestión de acordarse de borrar. Es cuestión de que **borrar sea inevitable**. Hay cinco mecanismos superpuestos.

### 1. Las imágenes y GIFs nunca tocan el disco

`download/http_engine.py` los descarga a un `bytearray` en memoria y los pasa directamente a Telegram. No hay fichero temporal en ningún momento del recorrido.

### 2. Los videos viven en un workspace que se borra en `finally`

Los videos sí necesitan un fichero: yt-dlp y ffmpeg requieren poder hacer *seeking*, y eso no se puede hacer sobre un stream. Pero nacen dentro de un `EphemeralWorkspace`:

```python
with EphemeralWorkspace(root) as workspace:
    ...trabajo con el fichero...
# aquí el directorio ya no existe
```

El `__exit__` borra siempre y no captura la excepción, así que el borrado ocurre en el retorno normal, en una excepción, y en un `asyncio.CancelledError`. **No existe una ruta de código que salga de ese bloque sin pasar por el borrado.**

Y borra con insistencia: en Windows es habitual que un fichero siga bloqueado unos milisegundos después de que ffmpeg cierre, o que quede marcado como solo lectura. Un único `rmtree` fallaría justo ahí. Por eso reintenta tres veces quitando el flag de solo lectura entre intentos.

### 3. En Docker el workspace está en RAM

`docker-compose.yml` monta el workspace como **tmpfs**:

```yaml
tmpfs:
  - /tmp/scrappy:size=512m,mode=1777
```

Es decir: aunque los cuatro mecanismos restantes fallaran a la vez, el video **nunca llegó a escribirse en el disco físico**. Estaba en memoria. Al parar el contenedor desaparece.

### 4. Un handler de señales purga antes de morir

`SIGTERM` (lo que envía `docker stop`), `SIGINT` (Ctrl+C) y `SIGBREAK` disparan `purge_active()`, que borra todos los workspaces vivos, y después reenvían la señal para no alterar el código de salida.

### 5. Al arrancar se barren los huérfanos

`sweep_orphans()` borra los directorios `scrappy-ws-*` que un crash anterior pudiera haber dejado. Solo toca lo que lleva ese prefijo: nada más del directorio temporal.

---

## Un detalle que costó encontrar

`InputFile` de python-telegram-bot lee el stream entero **pero no lo cierra**. Pasarle un fichero abierto dejaba un handle vivo hasta el siguiente paso del recolector de basura, y en Windows ese handle impide borrar el directorio.

Es decir: el borrado habría fallado en silencio y el video se habría quedado ahí. Por eso `publisher._input_file()` lee los bytes y los pasa ya leídos. Hay un test dedicado a esto (`test_publicar_desde_fichero_no_deja_el_handle_abierto`).

Lo cuento porque ilustra el tipo de fallo del que hay que protegerse aquí: no el olvido evidente, sino el efecto secundario de una librería de terceros.

---

## Qué sí persiste

Para no publicar el mismo meme cada tres horas, el bot necesita recordar qué publicó. Eso son hashes e identificadores, no contenido:

```sql
uid, source, source_id, permalink, sha256, phash,
score, kind, telegram_message_id, telegram_file_id, published_at
```

Unos **200 bytes por item**. Publicando 5 memes cada 3 horas, ~3 MB al año.

El `telegram_file_id` merece una mención: es el identificador que devuelve Telegram al recibir un medio, y permite reenviarlo a otro chat sin volver a descargarlo de la fuente original. Es lo que hace que "Telegram es el archivo" sea literal y no una metáfora.

### Tres niveles de privacidad

`SCRAPPY_STATE_BACKEND` elige cuánto se recuerda:

| Valor | Qué guarda | Cuándo usarlo |
|---|---|---|
| `sqlite` *(por defecto)* | Metadatos en `data/scrappy.db` | Uso normal. Es el único que evita repeticiones entre ejecuciones. |
| `memory` | Nada en disco; solo recuerda durante la ejecución | No quieres ningún rastro en la máquina y aceptas que el bot repita entre runs. |
| `none` | Nada | Máxima privacidad, máxima repetición. |

---

## Cómo comprobarlo tú mismo

### Con la CLI

```bash
scrappy health
```

Incluye la línea `workspaces activos: 0 (limpio)`. Si dice otra cosa y no hay ninguna descarga en curso:

```bash
scrappy purge
```

### Buscando restos a mano

Tras un run real, en el árbol del proyecto no debe haber ni un medio:

```bash
find . -type f \( -name '*.mp4' -o -name '*.webm' -o -name '*.gif' \) -not -path './.git/*'
```

En PowerShell:

```bash
Get-ChildItem -Recurse -Include *.mp4,*.webm,*.gif | Where-Object { $_.FullName -notlike "*\.git\*" }
```

### Con los tests

Dos tests lo verifican, y son los más importantes del proyecto:

```bash
pytest tests/integration/test_pipeline.py -k "no_queda_ningun_fichero" -v
```

- `test_no_queda_ningun_fichero_tras_un_run` — cinco items publicados, cero ficheros después.
- `test_no_queda_ningun_fichero_cuando_falla_la_publicacion` — el caso peligroso: el medio ya está escrito y el envío revienta.
- `test_no_queda_ningun_fichero_cuando_falla_la_descarga` — falla antes de terminar.

Escriben ficheros de verdad, no simulados. Si el pipeline se dejara algo, el fichero seguiría ahí al terminar y el test fallaría.

CI además ejecuta los tests en **Windows** aparte de Linux, precisamente porque el borrado se comporta distinto allí, y tiene un paso que falla el build si aparece cualquier `.mp4`/`.webm`/`.gif` en el árbol tras los tests.

### Observando el contenedor en vivo

```bash
docker compose exec scrappy ls -la /tmp/scrappy
```

Durante una descarga verás un directorio `scrappy-ws-*`. Segundos después, nada.

---

## Consecuencias que conviene aceptar

Esta restricción tiene un precio y es honesto decirlo:

1. **No hay reintentos baratos.** Si Telegram rechaza un video, hay que volver a descargarlo desde la fuente. La alternativa —cachearlo— es exactamente lo que no se hace.
2. **Cada ejecución vuelve a bajar lo que decida publicar.** No hay caché entre runs. El coste es ancho de banda; el beneficio es que no hay una carpeta con gigas de memes en tu disco.
3. **Un clip que no cabe en 50 MB se transcodifica en el momento**, cada vez, en vez de guardarse ya convertido.

Son costes reales y deliberados. La razón de fondo está en [adr/0008-almacenamiento-efimero.md](adr/0008-almacenamiento-efimero.md).
