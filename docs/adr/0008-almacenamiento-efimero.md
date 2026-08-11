# ADR-0008: Almacenamiento efímero, Telegram como único archivo

**Estado:** aceptada · **Fecha:** 2026-08-11

Este es el ADR más importante del proyecto: condiciona el diseño de casi todo lo demás.

## Contexto

Un bot que descarga videos y memes acumula ficheros con enorme facilidad. La opción por defecto de cualquier implementación es guardarlos: en una carpeta, en una caché, "por si acaso". Eso trae ventajas evidentes —reintentos baratos, no volver a bajar lo mismo, poder republicar— y un montón de problemas menos evidentes:

- **Crece sin límite.** Publicando 40 clips al día, son decenas de GB al año.
- **Es contenido de terceros almacenado en tu máquina.** Con implicaciones distintas a las de simplemente retransmitirlo.
- **Hay que gestionarlo**: rotación, límites, purgas, alguien que se acuerde.
- **Es el fallo silencioso más probable.** Un bug de limpieza no rompe nada visible; solo llena el disco durante meses.

El requisito explícito para este proyecto fue: **el contenido no se conserva en la máquina, solo en Telegram**.

## Decisión

El medio **nunca se archiva localmente**. Cada item se descarga, se publica y se borra en el mismo ciclo. Telegram es el único archivo.

Concretamente:

1. **Imágenes y GIFs viajan en memoria** (`bytearray`), sin fichero temporal en ningún punto.
2. **Los videos usan un fichero temporal** —yt-dlp y ffmpeg necesitan *seeking*— dentro de un `EphemeralWorkspace` que borra en su `finally`.
3. **Las etapas descargar→publicar→borrar se ejecutan por item, no por lote**, de modo que nunca hay más de un medio materializado.
4. **En Docker el workspace es un tmpfs**: el video ni siquiera llega al disco físico.
5. **Un handler de señales purga** antes de que el proceso muera; **un barrido al arrancar** limpia lo que un crash dejara.
6. **Se guarda el `file_id` que devuelve Telegram** en lugar del fichero, lo que permite reenviar el medio sin volver a descargarlo de la fuente.
7. **Solo persisten metadatos** de deduplicación (~200 B/item), y se pueden desactivar con `STATE_BACKEND=memory` o `=none`.

## Alternativas descartadas

**Caché con expiración (LRU o TTL).** Es la solución convencional y funciona. Se descartó porque no cumple el requisito: una caché de 24 horas sigue siendo contenido de terceros almacenado durante 24 horas. Además reintroduce todo lo que se quería evitar: política de tamaño, purga, y un modo de fallo silencioso.

**Descargar todo el lote y publicarlo después.** Algo más rápido —las descargas se solaparían— pero significa N medios en disco a la vez. El ahorro no justifica multiplicar por N la superficie del problema.

**Todo en memoria, también el video.** Sería lo ideal, pero yt-dlp escribe a fichero y ffmpeg necesita hacer seeking sobre un fichero real. Forzarlo requeriría reescribir cosas que no nos toca reescribir. El tmpfs consigue el mismo efecto práctico —el video vive en RAM— sin pelearse con las herramientas.

**No guardar ni los hashes.** Es la opción de máxima privacidad y **está disponible** (`STATE_BACKEND=none`), pero no como defecto: sin memoria de lo publicado, el bot repite el mismo meme cada tres horas, que es exactamente lo que un curador no debe hacer. Se ofrece como elección informada en vez de imponerla.

## Consecuencias

### A favor

- El uso de disco es constante y trivial, sea cual sea el tiempo que lleve funcionando.
- No hay una carpeta con gigas de contenido ajeno en tu máquina.
- No hay política de retención que gestionar ni purga que programar.
- El `file_id` de Telegram permite reenviar sin volver a descargar.

### En contra

- **No hay reintentos baratos.** Si Telegram rechaza un video, hay que volver a bajarlo de la fuente.
- **Cada ejecución vuelve a descargar lo que publica.** Sin caché entre runs. Cuesta ancho de banda.
- **Los clips grandes se transcodifican en el momento, cada vez.**
- **El código es más delicado.** Cada ruta de error tiene que borrar. Por eso el borrado no está en el código de negocio sino en un `finally` del que no se puede escapar, y por eso hay tests dedicados a verificarlo en el camino feliz, en el fallo de descarga y en el fallo de publicación.

### Verificación

La garantía se prueba, no se asume. `tests/integration/test_pipeline.py` escribe ficheros reales y comprueba que no queda ninguno tras un run exitoso, tras uno que falla al descargar y tras uno que falla al publicar. CI ejecuta esos tests también en **Windows**, donde el borrado se comporta distinto, y falla el build si aparece cualquier `.mp4`/`.webm`/`.gif` en el árbol.

Un fallo real encontrado gracias a esto: `InputFile` de python-telegram-bot lee el stream pero no lo cierra, y el handle abierto impedía borrar el directorio en Windows. Ver [EPHEMERAL_STORAGE.md](../EPHEMERAL_STORAGE.md).
