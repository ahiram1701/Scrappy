# Uso legal y responsable

**Léelo antes de activar nada más que Reddit.** No es un descargo de responsabilidad de trámite: hay decisiones aquí que pueden costarte una cuenta bloqueada o un aviso de retirada.

> Esto no es asesoramiento jurídico. Es una explicación honesta de dónde está cada cosa para que decidas con información.

---

## Lo esencial en cinco frases

1. El **código** de Scrappy es MIT. El **contenido** que descubre **no es tuyo ni mío**: es de quien lo creó.
2. **Reddit** tiene API oficial documentada. Usarla como la usa Scrappy está permitido.
3. **X en modo scrape, TikTok e Instagram** no ofrecen API pública para este caso de uso. Obtener contenido de ellas **incumple sus términos de servicio**.
4. Por eso esas fuentes vienen **desactivadas** y hace falta poner `SCRAPPY_ENABLE_TOS_RISKY_SOURCES=true` a mano. El proyecto no lo hace por ti, deliberadamente.
5. Scrappy **siempre** acredita al autor y enlaza al original. Eso es lo mínimo exigible, pero **no equivale a un permiso**.

---

## Fuente por fuente

### Reddit — vía limpia ✅

Reddit publica una [API documentada](https://www.reddit.com/dev/api/) con OAuth. Scrappy la usa tal cual, con el flujo *application-only*, respetando los límites de tasa y con un User-Agent identificable.

Requisitos que Scrappy cumple:
- User-Agent descriptivo y único (Reddit devuelve 429 si no).
- Respeto del rate limit, con parada ante un 429.
- Sin evadir restricciones de subreddits privados.

Ten en cuenta que el contenido de Reddit lo suben usuarios, y **muchas veces no son sus autores originales**. Un meme en r/memes puede ser el trabajo de alguien que no tiene ni idea de que está ahí.

### X / Twitter — depende del backend ⚠️

| Backend | Situación |
|---|---|
| `api` | Vía oficial. **Pero el tier gratuito no permite buscar posts**: el endpoint de búsqueda requiere el tier Basic, que en el momento de escribir esto ronda los **200 USD al mes**. Con el gratuito recibirás 403. |
| `scrape` | Enumera perfiles con yt-dlp y cookies. No cuesta dinero, pero **incumple los términos de X**, se rompe cuando la plataforma cambia por dentro, y puede acarrear el bloqueo de la cuenta cuyas cookies uses. |

El precio del tier de pago es la razón real por la que existe el backend `scrape`. Es tu decisión cuál usar; el proyecto no la toma por ti.

### TikTok — incumple los ToS ❌

TikTok no ofrece API pública para descubrir y descargar contenido viral. La única vía es enumerar hashtags y perfiles con yt-dlp.

- Incumple los [Términos de servicio de TikTok](https://www.tiktok.com/legal/terms-of-service).
- Se rompe con frecuencia: TikTok cambia sus mecanismos internos cada pocas semanas.
- Puede acarrear bloqueo de IP o de la cuenta cuyas cookies uses.

### Instagram — incumple los ToS, y es el más restrictivo ❌

Instagram exige sesión iniciada para prácticamente todo, así que aquí las cookies **no son opcionales**.

- Incumple los [Términos de Meta](https://help.instagram.com/581066165581870).
- Usar tu cuenta personal para esto puede acabar con esa cuenta **limitada o bloqueada**. No uses una cuenta que te importe.

---

## Copyright: la parte que la gente pasa por alto

Que un contenido sea público no significa que sea libre. Un meme, un clip, un video corto: **casi todo tiene autor y casi todo tiene copyright**, aunque nadie ponga un aviso.

Scrappy incluye **siempre** en cada publicación:
- El nombre del autor tal y como lo reporta la plataforma.
- Un enlace a su perfil, cuando existe.
- Un enlace al post original.

Esa atribución **no se puede desactivar**. No hay opción de configuración para quitarla, y es intencionado.

Pero conviene ser claro: **la atribución no es una licencia**. Acreditar a alguien no te da derecho a republicar su trabajo. Lo que sí hace es (a) ser lo mínimo decente, (b) mandar tráfico al autor en vez de quitárselo, y (c) permitir que cualquiera llegue al original.

### El riesgo escala con el alcance

| Uso | Riesgo realista |
|---|---|
| Canal privado, tú y cuatro amigos | Muy bajo. Es equivalente a mandar enlaces por WhatsApp. |
| Canal público pequeño | Bajo, pero ya estás redistribuyendo. |
| Canal público grande | Real. Puedes recibir avisos de retirada. |
| **Monetizado** | **Alto.** Estás lucrándote con trabajo ajeno sin licencia. |

Ese último caso es cualitativamente distinto de los demás. Si vas a monetizar, necesitas permisos, no atribución.

---

## Si recibes un aviso de retirada

1. **Retira el contenido primero.** Borra el mensaje en Telegram. Discutir viene después.
2. **Añade al autor a la lista de bloqueo** para no volver a publicarlo:
   ```yaml
   filters:
     blocked_authors: [nombre_del_autor]
   ```
3. **Responde a quien reclama** confirmando la retirada.
4. Si la reclamación te parece infundada, busca asesoramiento antes de contestar. No improvises.

Scrappy guarda el `permalink` y el autor de todo lo publicado, así que puedes trazar de dónde salió cada cosa.

---

## Datos personales

- Scrappy guarda nombres de autor y URLs públicas: son metadatos necesarios para la atribución.
- **No** recoge datos privados, ni perfiles de usuarios, ni cruza información entre plataformas.
- Si alguien te pide que retires su contenido, es una petición legítima. Atiéndela.
- En la UE, si publicas en un canal abierto, puedes estar tratando datos personales bajo el RGPD. Con un canal privado entre conocidos, normalmente no.

---

## Seguridad de las cookies

Si usas las fuentes que requieren cookies, ten presente que **un fichero de cookies es equivalente a tu contraseña**:

- Nunca lo subas al repositorio. `.gitignore` ya bloquea `*.cookies.txt` y `secrets/`, pero revisa antes de cada commit.
- Móntalo en Docker como `:ro`.
- Usa una cuenta secundaria, nunca la principal.
- Si sospechas que se ha filtrado, cierra sesión en todos los dispositivos desde la plataforma. Eso invalida las cookies.

---

## Recomendación

Si tuviera que resumir en un párrafo lo que haría yo:

**Empieza solo con Reddit, en un canal privado.** Es la configuración por defecto y no plantea ningún problema. Da muy buen material. Si después necesitas más y aceptas los riesgos que se explican aquí, activa el resto entendiendo qué estás activando.

Lo que no recomiendo a nadie: activar las cuatro fuentes en un canal público monetizado. Eso combina el máximo riesgo técnico con el máximo riesgo legal.

---

## Responsabilidad

Scrappy es una herramienta. Como cualquier herramienta, su uso es responsabilidad de quien la usa. El proyecto:

- Viene con las fuentes problemáticas **desactivadas**.
- Exige un **flag explícito** para activarlas.
- Muestra un **aviso en el arranque** cuando ese flag está activo.
- **Documenta** aquí lo que implica cada una.
- Hace la atribución **obligatoria y no desactivable**.

Lo que haga cada quien a partir de ahí es cosa suya. El código se distribuye sin garantía, como dice la [licencia](../LICENSE).
