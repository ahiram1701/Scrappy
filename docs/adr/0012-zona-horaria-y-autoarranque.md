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

### Una tarea de inicio de sesión, con un respaldo

Módulo `autostart.py` con `status()`, `enable()` y `disable()`. En Windows intenta primero registrar una tarea con `schtasks`; fuera de Windows detecta que no aplica y remite a la unidad de systemd.

**Enmienda 1 (agosto de 2026).** La tarea sola no bastaba. Crear una tarea escribe en la carpeta raíz del Programador, y eso Windows solo se lo permite a un proceso **elevado**. Ser administrador no basta: el token de una sesión normal lleva el grupo de administradores «solo para denegar» hasta que algo pide elevación, y la TUI no la pide. Por eso el botón «Activar» respondía siempre «Acceso denegado» en la máquina donde se probó, que sí es una cuenta de administrador. Se comprobó también con una tarea mínima (`schtasks /Create /TR notepad.exe`) para descartar que fuera el XML: mismo resultado.

**Enmienda 2 (agosto de 2026).** Arrancar al iniciar sesión no es lo que casi nadie quiere: con el equipo encendido y la sesión cerrada, Scrappy no publica. Se añade el modo **`sistema`**, una tarea con `BootTrigger` que corre sin que entre nadie. Eso no tiene respaldo posible —una tarea sin sesión solo se registra elevada— así que el modo pide UAC de forma explícita, con `Start-Process -Verb RunAs`, y dice qué hacer si se rechaza. La identidad es `S4U` («esta cuenta, sin sesión y sin guardar la contraseña») con `SYSTEM` de respaldo en la misma llamada elevada, porque dos diálogos de UAC seguidos para una sola acción se parecen demasiado a algo que no deberías aceptar.

Ahora, cuando el Programador dice que no, se cae a un **acceso directo en la carpeta de Inicio del usuario**, que es suya y no pide permiso a nadie. La tarea se sigue intentando primero porque hace dos cosas que el acceso directo no: retrasa el arranque un minuto y reintenta si el proceso muere. Lo primero se compensa en `scrappy run`, que ahora insiste con Telegram en vez de rendirse al primer intento.

## Alternativas descartadas

### Para la zona horaria

**Dejarlo en UTC y explicarlo en la documentación.** Es lo que había. Obliga a hacer aritmética mental cada vez que se mira el panel, y nadie lee la documentación antes de leer una hora.

**Guardar en hora local.** Tentador porque simplifica la presentación, y una fuente clásica de errores que solo se manifiestan dos veces al año: en los cambios de horario hay instantes que se repiten y otros que no existen. Un `published_at` local no se puede ordenar con fiabilidad.

**Configurar un desfase en horas** (`SCRAPPY_UTC_OFFSET=-6`) en vez de una zona IANA. Más fácil de entender y equivocado la mitad del año, precisamente por el horario de verano.

### Para el autoarranque

**Un acceso directo en la carpeta Inicio.** ~~Es lo más simple, y abre una ventana de consola visible al iniciar sesión, todos los días. Tampoco permite fijar el directorio de trabajo, que aquí importa: `.env` y `config/` se leen relativos a él.~~ **Las dos razones eran falsas** y por eso hoy es el respaldo: un `.lnk` que apunta a `pythonw.exe` no abre ninguna ventana, y `WorkingDirectory` es un campo del propio acceso directo. Lo que sí es cierto es lo que se pierde frente a la tarea —el retraso de un minuto y el reintento— y por eso va segundo y no primero.

**Un servicio de Windows.** Corre sin sesión iniciada, que es más de lo que hace falta, y a cambio necesita privilegios de administrador para instalarse y un envoltorio del estilo de NSSM para un proceso Python. Para «que arranque cuando entro a mi equipo» es desproporcionado.

**`schtasks /Create /TR "..."` con modificadores sueltos**, sin XML. Es lo que uno escribiría primero, y no permite fijar el directorio de trabajo. Se puede rodear con `cmd /c cd /d ... && ...`, que reintroduce la ventana de consola que se quería evitar. El XML permite fijar directorio, nivel de privilegio y política de instancias de una vez.

**Un `.vbs` intermedio** para ocultar la ventana, que es la receta habitual. Consigue lo mismo que `pythonw.exe` a cambio de un fichero más que mantener y que puede desaparecer sin que nadie lo note.

**Lanzar la TUI en vez del bot.** Sería coherente con cómo se abre a mano, y significaría una ventana de interfaz abriéndose en cada inicio de sesión. Lo que hace falta de fondo es el scheduler.

## Consecuencias

**Buenas.** Las horas del panel, de `/start` y de `/config` se leen sin traducir nada. La zona se detecta sola, así que la mayoría no tiene que configurar nada. El autoarranque funciona sin administrador —por el respaldo— y se quita con el mismo botón que lo puso, que borra los dos mecanismos aunque solo hubiera uno puesto.

**Malas.** `autostart.py` tiene una rama por sistema operativo, y solo una está implementada. Detectar si la tarea existe obliga a **mirar el texto** que devuelve `schtasks`, porque usa el mismo código de salida para «no existe» que para «algo falló»; ese texto viene en el idioma del sistema, así que hay una lista de frases conocidas que puede quedarse corta con otro idioma o versión. Se probó contra un Windows en español y la frase no era la traducción literal de la inglesa, que es justamente por lo que la lista existe.

**A vigilar.** Lo que escribe la tarea del modo `sistema` corre bajo una sesión de inicio distinta de la interactiva, y los workspaces que deje un proceso muerto a mitad **no los puede borrar la sesión del usuario**: `scrappy purge` desde la TUI responde «Acceso denegado». Se comprobó matando la tarea a mitad de una descarga. No pasa en funcionamiento normal —el propio proceso borra su workspace en el `finally`— pero conviene saberlo antes de perseguir un fantasma.

**A vigilar.** El proceso que arranca solo corre con `pythonw.exe`, donde `sys.stdout` es `None`. Cualquier cosa que escriba en la consola o le pregunte por `isatty()` muere ahí, sin ventana donde verlo: es exactamente lo que pasaba, y por eso `configure_logging` manda los logs a `data/scrappy.log` cuando no hay consola. Lo que se escriba en el arranque conviene probarlo lanzándolo sin consola, no desde una terminal.

**A vigilar.** Con el autoarranque activo y el scheduler de la TUI a la vez hay dos procesos publicando. La deduplicación impide repetidos, pero se publica el doble de a menudo; el panel avisa cuando detecta esa combinación.
