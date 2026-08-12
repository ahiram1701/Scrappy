# ADR-0011: Un solo módulo de diagnóstico para tres consumidores

**Estado:** aceptada · **Fecha:** 2026-08-12

## Contexto

Configurar el bot por primera vez costó **varias rondas de depuración** por dos erratas que ninguna herramienta detectaba:

1. Al pegar el token, no se sustituyó la línea entera, y quedó el valor de ejemplo de la plantilla (`123456789:`) delante del token bueno. Telegram respondía «token rejected», sin más pista.
2. El chat id se copió con el `-` del ejemplo, que era de un canal. Para un chat privado va positivo, y Telegram respondía «Chat not found».

Ambas se descubrieron leyendo un traceback. Las dos son erratas de un carácter, y las dos las **indujo la propia plantilla** del proyecto: su valor de ejemplo era un id de canal negativo, y el token de ejemplo empezaba por un bloque numérico idéntico en forma al real.

Eso es un fallo del proyecto, no del usuario.

## Decisión

Un módulo único, **`src/scrappy/diagnostics.py`**, que devuelve `Check(nombre, estado, detalle, fix)`.

**Por qué uno y no tres.** Lo consumen `scrappy doctor`, el `/start` del bot y el asistente de la TUI. Los tres hacen exactamente las mismas preguntas —¿el token vale?, ¿el bot alcanza el chat?, ¿hay ffmpeg?— y escritas por separado se desincronizarían a la primera comprobación nueva. Cada consumidor solo lo pinta a su manera: tabla con `rich`, mensaje de Telegram, o modal de Textual.

**El campo `fix` es el que importa.** Decir qué falló sin decir qué hacer es exactamente lo que convirtió dos erratas en varias rondas de depuración. Cada comprobación lleva su línea de arreglo, y se muestra solo cuando algo va mal.

**Las comprobaciones salen de fallos reales.** No de imaginar lo que podría salir mal: el token con más de un `:`, el chat id negativo que no empieza por `-100`, el User-Agent de Reddit sin `/u/` (causa de los 429), ffmpeg ausente. Los dos primeros tienen un test que reproduce el caso exacto que ocurrió.

**No se llama a Telegram si el formato ya está mal.** Gastar una petición para que la rechacen por una errata evidente no aporta nada, y además el error de red taparía el diagnóstico útil.

## Decisiones asociadas

**`scrappy init` valida contra Telegram mientras preguntas.** Copiar una plantilla y cruzar los dedos es justo lo que falló. El asistente comprueba el token con `getMe` y el chat con `getChat` antes de escribir nada, con hasta tres intentos y una pista distinta según la forma del id: si es positivo sugiere pulsar Start, si empieza por `-100` sugiere añadir el bot como administrador.

**El editor de `.env` preserva comentarios**, igual que ruamel hace con el YAML ([ADR-0010](0010-tui-con-textual.md)). No hay un ruamel para dotenv, pero tampoco hace falta: una línea por variable, conservando lo que no se toca. Y los valores **nunca** se registran en el log, ni en DEBUG: el redactor de `logging.py` cubre los diccionarios, pero no intentarlo siquiera es más barato que confiar en él.

**El asistente de la TUI solo aparece si algo bloquea.** Los avisos por sí solos no lo justifican: interrumpir a quien únicamente tiene el User-Agent de Reddit sin personalizar sería molesto. Y se puede desactivar con `show_wizard=False`.

## Alternativas descartadas

**Validar en `Settings` con validadores de pydantic.** Es donde uno lo pondría primero, pero un validador que falla impide *arrancar*: no podrías abrir la TUI para arreglar el valor que impide abrir la TUI. El diagnóstico tiene que poder describir una configuración rota sin negarse a funcionar.

**Un único `scrappy doctor` sin módulo compartido.** Habría dejado al `/start` y a la TUI sin diagnóstico, que es justo donde el usuario está cuando descubre que algo no va.

**Comprobar solo contra Telegram, sin validación de formato.** Más simple, pero el mensaje de Telegram («token rejected») es precisamente el que no explica nada. La comprobación de formato es la que identifica *cuál* de las dos erratas es.

## Consecuencias

- `scrappy doctor` verificado en vivo: nueve comprobaciones en verde contra una configuración real que horas antes tenía los dos fallos.
- El diagnóstico aparece en tres sitios sin duplicarse. Añadir una comprobación la propaga a los tres.
- Queda una asimetría deliberada: el editor de YAML **no crea claves** y el de `.env` **sí**. El YAML tiene estructura que romper y sus secciones ya vienen en la plantilla; el `.env` es una lista plana donde una variable ausente es normal al añadir una opción nueva.
