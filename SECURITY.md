# Política de seguridad

## Reportar una vulnerabilidad

**No abras una issue pública.**

Usa [GitHub Security Advisories](https://github.com/Ahiram/Scrappy/security/advisories/new), que permite un reporte privado.

Incluye: qué falla, cómo reproducirlo, qué impacto tiene y, si la tienes, una idea de cómo arreglarlo.

Respuesta esperada en unos días. Este es un proyecto pequeño mantenido en tiempo libre; se atiende, pero no hay un SLA.

---

## Superficie de ataque

Lo que conviene tener presente sobre por dónde entra lo no confiable:

### Contenido de internet

Todo lo que llega de las plataformas es **entrada no confiable**: títulos, nombres de autor, URLs y los propios ficheros de medio.

Mitigaciones en el código:

- **Escapado de HTML en dos modos.** Los títulos se escapan como contenido; las URLs que van dentro de `href="..."` se escapan **con comillas**, porque una URL con `"` podría cerrar el atributo e inyectar los suyos. Hay un test dedicado a esto.
- **ffmpeg se invoca sin shell**, con argumentos fijos y listas de argumentos. No hay vía de inyección desde el título ni desde la URL de un post.
- **Límites de tamaño** en la descarga, comprobados dos veces: por `Content-Length` y acumulando trozos, porque muchos servidores no envían la cabecera.
- **Timeouts** en toda operación de red y en toda invocación de ffmpeg.

### Secretos

- Los tokens se guardan como `SecretStr`: no aparecen en un `repr` accidental.
- El logging **redacta** cualquier clave que parezca un secreto (`token`, `cookie`, `client_secret`…) antes de escribir.
- `/config` devuelve la configuración con los secretos ya redactados, para que se pueda pegar en una issue sin riesgo.
- `.gitignore` bloquea `.env`, `*.cookies.txt` y `secrets/`.

### Ficheros de cookies

Si usas las fuentes que los requieren: **un fichero de cookies equivale a tu contraseña**. Cualquiera que lo tenga entra en esa cuenta.

- Nunca lo subas al repositorio.
- Móntalo en Docker como solo lectura (`:ro`).
- Usa una cuenta secundaria, nunca la principal.
- Si sospechas que se ha filtrado, cierra sesión en todos los dispositivos desde la plataforma: eso invalida las cookies.

### Ejecución

- El contenedor corre como **usuario sin privilegios** (uid 1000).
- `no-new-privileges` activado.
- Sin puertos entrantes: el bot usa polling, no webhooks.
- Límite de memoria para que un medio patológico no se coma la máquina.
- La unidad de systemd de ejemplo trae `ProtectSystem=strict` y `ReadWritePaths` acotado a un solo directorio.

### Comandos del bot

- Solo responden a los ids de `SCRAPPY_TELEGRAM_ADMIN_IDS`.
- **Si la lista está vacía, nadie puede usar comandos.** Es deliberado: un bot recién configurado no debe quedar abierto a todo el mundo por descuido.
- Los intentos no autorizados se registran.

---

## Buenas prácticas al desplegar

1. `chmod 600 .env`.
2. Una cuenta secundaria para las cookies, si las usas.
3. Actualiza yt-dlp con regularidad: además de arreglar extractores, corrige fallos de seguridad.
4. Mantén el canal privado mientras estés probando.
5. Revisa los logs de vez en cuando buscando `unauthorized_command`.

---

## Versiones soportadas

Solo la última versión de `main`. El proyecto aún no tiene releases con soporte a largo plazo.
