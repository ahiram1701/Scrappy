# ADR-0009: Reddit por feeds Atom en vez de la API oficial

**Estado:** aceptada · **Fecha:** 2026-08-11 · **Sustituye a:** [ADR-0002](0002-httpx-en-vez-de-praw.md)

## Contexto

[ADR-0002](0002-httpx-en-vez-de-praw.md) decidió usar la API oficial de Reddit con OAuth *application-only*. Esa decisión ya no es aplicable:

- **Noviembre de 2025**: Reddit cerró el registro autoservicio de aplicaciones en `/prefs/apps`. El acceso pasa por un formulario de aprobación manual bajo la *Responsible Builder Policy*, y los proyectos personales se rechazan de forma sistemática. La página de creación devuelve un 500 en vez de un mensaje claro.
- **28 de mayo de 2026**: los endpoints `.json` sin autenticar empezaron a devolver 403.

Comprobado contra Reddit real, no deducido de la documentación:

| URL | Resultado |
|---|---|
| `www.reddit.com/r/memes/top.json?t=day` | 403 |
| `www.reddit.com/r/memes/top/.rss?t=day` | 403 |
| `old.reddit.com/r/memes/top/.rss?t=day` | 302 → pantalla de login |
| **`www.reddit.com/r/memes/.rss?sort=top&t=day`** | **200, `application/atom+xml`, 25 entradas** |

## Decisión

Usar los **feeds Atom públicos**, que Reddit nunca incluyó en la superficie de pago.

Cuatro consecuencias de diseño:

**1. El engagement sale de la posición en el feed.** El feed no informa de upvotes ni de comentarios, pero viene ordenado por calidad. La posición es la señal, y encaja incluso mejor que los upvotes brutos en el modelo de percentiles del scorer, porque la posición **ya es** un percentil ([ADR-0005](0005-ranking-por-percentiles.md)).

Con un matiz importante que costó descubrir: **el feed ignora `sort` y `t`**. Se comprobó mandándolos y comparando los ids devueltos —`?sort=top&t=day` y `?sort=new` dan la secuencia idéntica— y tampoco es orden cronológico, porque las edades no crecen de forma monótona. Lo que sirve es el listado `hot` del subreddit.

Eso no rompe el diseño: `hot` es la mezcla de votos y antigüedad que hace el propio Reddit, y para detectar lo que se está moviendo ahora es incluso mejor señal que el top del día. Pero sí tiene dos consecuencias prácticas:

- **No se envía ningún parámetro de orden**, porque mandar algo que el servidor ignora en silencio solo confunde a quien lea el código después. Tampoco hay claves de orden en `sources.yaml`: serían decorativas.
- **`hot` incluye posts de varios días**, así que un `SCRAPPY_MAX_AGE_HOURS` apretado descarta buena parte de lo que trae Reddit. En la primera prueba real con 48 h se filtraron 19 de 70 candidatos.

**2. Rotación de subreddits.** Sin autenticar, el límite ronda las 10 peticiones por minuto, y en la práctica saltan 429 incluso espaciando 7 segundos. En vez de consultar todos los subreddits en cada ronda, se recorre la lista por tramos con un desplazamiento derivado de la hora actual: con 9 subreddits y 3 por ronda, se cubre la lista entera cada tres ejecuciones, sin necesidad de guardar estado.

**3. Se valida que la raíz sea `<feed>`.** Cuando Reddit bloquea, devuelve su página de error con un 200 engañoso, y ese HTML puede parsear como XML sin quejarse. El resultado serían cero candidatos en silencio, que es el peor fallo posible porque parece que simplemente no había nada.

**4. Los posts fijados se degradan.** Los moderadores clavan anuncios arriba del subreddit y el feed los sirve en las primeras posiciones sin ninguna marca que los distinga. Como aquí la posición *es* el engagement, un anuncio fijado se llevaría la nota más alta del lote — observado en la primera prueba real, con un post fijado de hacía seis meses en el puesto 0. Se detectan por ser varias veces más viejos que la mediana del feed y se les asigna el engagement mínimo.

**`requires_tos_ack = False`.** RSS es una funcionalidad pública que Reddit sirve deliberadamente, y este adapter la consume como un lector de feeds educado: se identifica con un User-Agent descriptivo, respeta los 429 y espacia sus peticiones. No es equiparable al scraping de TikTok o Instagram.

## Alternativas descartadas

**Pedir la aprobación oficial.** Es la vía correcta y sigue disponible, pero los proyectos personales se rechazan de forma sistemática y las esperas van de días a semanas sin respuesta. No se puede pedir a alguien que monte un bot y espere un permiso que probablemente no llegue. El camino queda documentado por si a alguien se lo conceden.

**APIs de terceros de pago** que revenden datos de Reddit. Funcionan, pero convierten un bot personal en un gasto recurrente, y varias de las fuentes que anuncian la muerte de la API gratuita son precisamente quienes venden la alternativa.

**Eliminar Reddit del proyecto.** Fue la conclusión inicial, y era prematura: que el registro esté cerrado no significa que no queden vías. La insistencia del usuario en que las investigara fue lo que llevó a comprobarlo en vez de darlo por perdido.

**Falsear el User-Agent** para parecer un navegador. Evadiría parte del bloqueo, pero es exactamente lo que este proyecto no hace: identificarse honestamente es la contrapartida de usar un recurso público.

## Consecuencias

- **Reddit funciona sin credenciales.** Solo hace falta un User-Agent que identifique al usuario.
- **Menos metadatos**: sin upvotes reales, sin número de comentarios, sin duración y sin marca NSFW. La penalización por `low_comment_ratio` no se aplica a Reddit, y el filtro de NSFW depende de que los feeds públicos ya excluyan lo marcado.
- **Menos volumen por ronda**: 3 subreddits × 25 entradas en vez de los 9 × 60 que permitía la API.
- **Es terreno prestado.** Reddit ha dado a entender que RSS podría ser lo siguiente que cierre. Si un día empieza a devolver 403, el mensaje de error lo dice explícitamente y remite a este documento.
