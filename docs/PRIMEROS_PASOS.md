# Primeros pasos

De cero a tu primera publicación, en orden y sin saltar entre documentos. Unos veinte minutos, la mayoría esperando descargas.

Al terminar tendrás un bot que busca los mejores videos y memes, los publica en tu chat de Telegram y **no deja ni un byte de contenido en tu disco**.

---

## Lo que hace falta

| | Para qué | Cómo |
|---|---|---|
| **Python 3.11+** | Scrappy | [python.org](https://www.python.org/downloads/) |
| **ffmpeg** | Leer duración y resolución de los videos, y sacar frames para detectar reposts | `winget install Gyan.FFmpeg` |
| **Una cuenta de Telegram** | Recibir los memes | La que ya tienes |

Sin ffmpeg, Scrappy arranca y avisa, pero no puede publicar video.

**Alternativa: Docker.** Trae ffmpeg dentro y el workspace en RAM. Si te da igual una cosa u otra, [DEPLOYMENT.md](DEPLOYMENT.md) lo explica; esta guía va sin Docker.

---

## 1. Instalar

```bash
git clone https://github.com/Ahiram/Scrappy.git
cd Scrappy
python -m venv .venv
```

Activa el entorno —en Windows `.venv\Scripts\activate`, en Linux y macOS `source .venv/bin/activate`— e instala:

```bash
pip install -e ".[dev]"
```

Comprueba que ffmpeg está donde Scrappy lo va a buscar:

```bash
ffmpeg -version
```

Si dice que no lo encuentra después de instalarlo, cierra y abre la terminal: el `PATH` no se actualiza en las que ya estaban abiertas.

---

## 2. Crear el bot en Telegram

Habla con [@BotFather](https://t.me/BotFather) y manda `/newbot`. Te pedirá un nombre y un usuario acabado en `bot`. Al terminar te da el **token**, algo así:

```
8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
```

> ### ⚠️ El error del token
>
> El token es **una sola cadena, con un único `:`**. Al copiarlo de un ejemplo es fácil arrastrar el número de delante y acabar con dos: `123456789:8912040901:AAGQ...`. Telegram responde «Unauthorized» y nada más.
>
> Cuéntalos: **un solo `:`**.

Guarda el token a mano. Si lo pierdes, `/mybots` en BotFather te lo vuelve a enseñar.

---

## 3. Decidir a dónde publica

Dos opciones. Si dudas, empieza por la primera.

### A tu chat privado — lo más simple

1. Busca tu bot por su usuario y abre el chat.
2. **Pulsa «Iniciar».** Esto no es opcional: Telegram no permite que un bot escriba primero. Es la causa número uno de «el bot no me contesta».
3. Tu chat id es tu id de usuario, que te da [@userinfobot](https://t.me/userinfobot).

### A un canal — si quieres separarlo de tus chats

1. Crea un canal (puede ser privado).
2. Añade el bot como **administrador**, con permiso para publicar.
3. Reenvía cualquier mensaje del canal a [@userinfobot](https://t.me/userinfobot) para ver su id.

> ### ⚠️ El error del signo
>
> **Un chat privado tiene id positivo. Un canal, negativo y empezando por `-100`.**
>
> | Destino | Se ve así |
> |---|---|
> | Tu chat privado | `1412545148` |
> | Un canal o supergrupo | `-1001412545148` |
>
> Poner un `-` de más a un id de usuario da «Chat not found», y el mensaje no dice nada del signo. Copia el id **tal cual** te lo da @userinfobot: si no trae menos, no le pongas uno.

---

## 4. Configurar

```bash
scrappy init
```

Te pregunta lo imprescindible y **valida cada valor según lo escribes**: si el token lleva dos `:` o el chat id tiene un signo raro, te lo dice ahí mismo en vez de fallar tres pasos después.

<details>
<summary>Prefiero editar el fichero a mano</summary>

```bash
cp .env.example .env
cp config/sources.example.yaml config/sources.yaml
```

Lo mínimo en `.env`:

```ini
SCRAPPY_TELEGRAM_BOT_TOKEN=8912040901:AAGQ81ToRpm44qGQqeX5DE_sU7Jx2b0JOcU
SCRAPPY_TELEGRAM_TARGET_CHAT_ID=1412545148
SCRAPPY_TELEGRAM_ADMIN_IDS=1412545148
SCRAPPY_REDDIT_USER_AGENT=windows:scrappy:0.1.0 (by /u/tu_usuario)
```

Ese `tu_usuario` cámbialo por el tuyo de Reddit. No hacen falta credenciales —Scrappy usa los feeds públicos— pero sí identificarte: con un User-Agent genérico, Reddit responde 429 y esa fuente deja de aportar.

`SCRAPPY_TELEGRAM_ADMIN_IDS` es quién puede darle órdenes al bot. Vacío significa **nadie**, a propósito.

</details>

### Comprobar antes de seguir

```bash
scrappy doctor
```

No dice solo qué falla: dice **cómo arreglarlo**, con la orden exacta. Si sale algo en rojo, resuélvelo aquí; más adelante cuesta más entender de dónde viene.

```
                   Diagnostico de Scrappy
┌─────────┬──────────────────────┬──────────────────────────┐
│         │ comprobacion         │ detalle                  │
├─────────┼──────────────────────┼──────────────────────────┤
│ OK      │ Token del bot        │ formato correcto         │
│ OK      │ Chat destino         │ formato de chat privado  │
│ OK      │ Administradores      │ 1 autorizado(s)          │
│ OK      │ ffmpeg               │ disponible               │
│ OK      │ Fuentes activas      │ reddit, lemmy, bluesky   │
│ OK      │ User-Agent de Reddit │ identifica correctamente │
│ OK      │ config/sources.yaml  │ valido                   │
└─────────┴──────────────────────┴──────────────────────────┘

Todo correcto
```

Lo que sale como **FALLO** hay que arreglarlo; lo que sale como **AVISO** deja usar Scrappy pero conviene mirarlo. Con `--offline` no consulta a Telegram, solo revisa formatos.

---

## 5. Ver qué publicaría, sin publicar nada

Este paso no se salta. Es la diferencia entre estrenar el bot y estrenarlo publicando algo que no querías en un canal con gente.

```bash
scrappy fetch --dry-run
```

Ni descarga ni publica: solo enseña qué habría elegido y **por qué**.

```
                 Candidatos evaluados (no se descargo nada)
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ score ┃ fuente ┃ veredicto    ┃ engagement ┃ titulo                     ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 0.891 │ reddit │ SELECCIONADO │     48,213 │ He didn't expect that      │
│ 0.774 │ lemmy  │ SELECCIONADO │     22,904 │ Perfectly timed            │
│ 0.301 │ reddit │ nota baja    │      1,204 │ Meh                        │
│ 0.000 │ reddit │ demasiado…   │        890 │ 4 hour compilation         │
└───────┴────────┴──────────────┴────────────┴────────────────────────────┘
```

Tarda entre 15 y 40 segundos: la mayor parte es esperar a las plataformas, que limitan cuántas peticiones aceptan.

**Si sale vacío**, no es un fallo: normalmente es `min_score` demasiado alto o `max_age_hours` demasiado corto. [RANKING.md](RANKING.md) explica cómo calibrarlo.

---

## 6. Publicar el primero

```bash
scrappy fetch -n 1
```

Un solo item, para verlo llegar. Ve a Telegram: ahí está, con su autor acreditado, el enlace al original y tres botones debajo. Qué hace cada uno, en [TELEGRAM.md](TELEGRAM.md#los-botones-bajo-cada-publicación).

Comprueba también lo que no se ve:

```bash
scrappy health
```

```
workspaces activos: 0 (limpio)
```

Ese cero es la promesa central del proyecto. El video existió en tu disco el tiempo justo de subirlo. El [porqué y cómo verificarlo](EPHEMERAL_STORAGE.md), si te interesa.

---

## 7. La interfaz

Doble clic en **`Scrappy.bat`** —o `scrappy tui` desde la terminal—. Tres pantallas:

| Tecla | Pantalla | Para qué |
|---|---|---|
| `d` | **Panel** | Estado, fuentes, scheduler, arranque automático |
| `c` | **Candidatos** | Qué publicaría y por qué; también publicar |
| `s` | **Configuración** | Todo lo ajustable, sin abrir un fichero |
| `?` | **Ayuda** | Todas las teclas |

En **Candidatos**, pulsa `e` para explorar y luego muévete con las flechas: el panel derecho explica de dónde sale cada nota. Con `v` ves el mensaje exacto que llegaría a Telegram, sin mandarlo.

En **Configuración** están las ~50 opciones en pestañas, cada una con una explicación de **por qué** tocarla. Guardar con `Ctrl+S` aplica los cambios al momento: no hace falta reiniciar.

Guía completa en [TUI.md](TUI.md).

---

## 8. Que funcione solo

Hasta aquí, Scrappy publica cuando tú se lo dices. Para que lo haga por su cuenta hay dos piezas, y hacen falta las dos:

**El scheduler**, que publica cada N horas. Se arranca desde el Panel, y con `SCRAPPY_SCHEDULE_ENABLED=true` en el `.env` (ya viene así). Cada 3 horas, 5 items: unos 40 al día.

**Un proceso vivo.** Y aquí está lo que sorprende a todo el mundo:

> **Scrappy solo corre mientras hay un proceso suyo vivo.** Si lo abriste con doble clic y cierras la ventana, deja de publicar. No hay ningún servicio de fondo esperando — a menos que lo pongas tú.

En el Panel, sección **Arranque automático**, el botón «Activar» registra una tarea que arranca el bot al iniciar sesión, en segundo plano y sin ventana. No hace falta administrador. Se quita con el mismo botón.

Las cuatro formas de tenerlo corriendo, con sus ventajas, en [OPERATIONS.md](OPERATIONS.md#cuándo-corre-scrappy).

---

## Y ahora qué

| Si quieres… | Ve a |
|---|---|
| Saber qué hace cada botón de Telegram | [TELEGRAM.md](TELEGRAM.md) |
| Que elija mejor | [RANKING.md](RANKING.md) |
| Añadir más fuentes | [CONFIGURATION.md](CONFIGURATION.md) |
| Dejarlo en un servidor | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Entender cómo funciona por dentro | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Publicar fuera de un canal privado | **[LEGAL.md](LEGAL.md), antes de nada** |

---

## Los tres tropiezos habituales

**«El bot no me contesta.»** Casi siempre es que nunca pulsaste «Iniciar» en el chat con él, o que tu id no está en `SCRAPPY_TELEGRAM_ADMIN_IDS`. Y la tercera: que no hay ningún Scrappy corriendo.

**«Dejó de publicar de repente.»** ¿Cerraste la ventana? Ver el paso 8.

**«Publica de madrugada.»** Zona horaria. Mira la última línea de `/start`; si no es la tuya, pon `SCRAPPY_TIMEZONE` o déjalo vacío para que la detecte.

El resto, en [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Y ante cualquier cosa rara, **empieza por `scrappy doctor`**.
