# ADR-0012: UTC dentro, hora local fuera; y una tarea de sesión para el autoarranque

**Estado**: Aceptada · **Fecha**: 2026-08-12

## Contexto

Dos problemas distintos que aparecieron juntos, en la primera semana de uso real.

**El scheduler estaba fijado a UTC.** `AsyncIOScheduler(timezone="UTC")` y `next_run_at` devolviendo un ISO en esa zona. Para quien lo usaba desde `America/Mexico_City`, el panel anunciaba «próxima ronda: 2026-08-13T05:00+00:00» cuando en su reloj eran las 23:00 del día anterior. Con un intervalo en minutos el desfase no cambia *cuándo* se dispara, pero sí hace ilegible lo que se muestra — y convierte «publica de madrugada» en un misterio.

**Y no había forma de que Scrappy arrancase solo.** La forma habitual de usarlo es doble clic en `Scrappy.bat`, así que cerrar la ventana era dejar de publicar, sin que nada lo advirtiera. La documentación describía una unidad de systemd, que en un equipo de escritorio con Windows no ayuda.

## Decisión

### UTC dentro, hora local fuera

Nuevo `SCRAPPY_TIMEZONE`, vacío por defecto = detectar la del sistema con `tzlocal`. El scheduler dispara en esa zona y las horas se escriben en ella.

**Lo que se guarda sigue siendo UTC, sin excepción**: `published_at`, los logs y las comparaciones de antigüedad. La zona es solo presentación y disparo, y la conversión vive en un único módulo, `core/tiempo.py`.

Ese módulo además redacta las horas en lenguaje corriente — «hoy a las 21:20» en lugar de un ISO. Un ISO en UTC obliga a restar seis horas mentalmente para saber si eso es hoy o mañana.

`tzlocal` se declara como dependencia directa aunque ya llegue con APScheduler: apoyarse en una transitiva es apoyarse en un detalle de implementación ajeno.

### Una tarea de inicio de sesión

Módulo `autostart.py` con `status()`, `enable()` y `disable()`. En Windows registra una tarea con `schtasks`; fuera de Windows detecta que no aplica y remite a la unidad de systemd.

## Alternativas descartadas

### Para la zona horaria

**Dejarlo en UTC y explicarlo en la documentación.** Es lo que había. Obliga a hacer aritmética mental cada vez que se mira el panel, y nadie lee la documentación antes de leer una hora.

**Guardar en hora local.** Tentador porque simplifica la presentación, y una fuente clásica de errores que solo se manifiestan dos veces al año: en los cambios de horario hay instantes que se repiten y otros que no existen. Un `published_at` local no se puede ordenar con fiabilidad.

**Configurar un desfase en horas** (`SCRAPPY_UTC_OFFSET=-6`) en vez de una zona IANA. Más fácil de entender y equivocado la mitad del año, precisamente por el horario de verano.

### Para el autoarranque

**Un acceso directo en la carpeta Inicio.** Es lo más simple, y abre una ventana de consola visible al iniciar sesión, todos los días. Tampoco permite fijar el directorio de trabajo, que aquí importa: `.env` y `config/` se leen relativos a él.

**Un servicio de Windows.** Corre sin sesión iniciada, que es más de lo que hace falta, y a cambio necesita privilegios de administrador para instalarse y un envoltorio del estilo de NSSM para un proceso Python. Para «que arranque cuando entro a mi equipo» es desproporcionado.

**`schtasks /Create /TR "..."` con modificadores sueltos**, sin XML. Es lo que uno escribiría primero, y no permite fijar el directorio de trabajo. Se puede rodear con `cmd /c cd /d ... && ...`, que reintroduce la ventana de consola que se quería evitar. El XML permite fijar directorio, nivel de privilegio y política de instancias de una vez.

**Un `.vbs` intermedio** para ocultar la ventana, que es la receta habitual. Consigue lo mismo que `pythonw.exe` a cambio de un fichero más que mantener y que puede desaparecer sin que nadie lo note.

**Lanzar la TUI en vez del bot.** Sería coherente con cómo se abre a mano, y significaría una ventana de interfaz abriéndose en cada inicio de sesión. Lo que hace falta de fondo es el scheduler.

## Consecuencias

**Buenas.** Las horas del panel, de `/start` y de `/config` se leen sin traducir nada. La zona se detecta sola, así que la mayoría no tiene que configurar nada. El autoarranque no pide administrador y se quita con el mismo botón que lo puso.

**Malas.** `autostart.py` tiene una rama por sistema operativo, y solo una está implementada. Detectar si la tarea existe obliga a **mirar el texto** que devuelve `schtasks`, porque usa el mismo código de salida para «no existe» que para «algo falló»; ese texto viene en el idioma del sistema, así que hay una lista de frases conocidas que puede quedarse corta con otro idioma o versión. Se probó contra un Windows en español y la frase no era la traducción literal de la inglesa, que es justamente por lo que la lista existe.

**A vigilar.** Con el autoarranque activo y el scheduler de la TUI a la vez hay dos procesos publicando. La deduplicación impide repetidos, pero se publica el doble de a menudo; el panel avisa cuando detecta esa combinación.
