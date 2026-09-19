# Video Generation

Pipeline personal para un canal de YouTube en español de psicología / desarrollo
personal / motivación, **sin presentador en cámara**: narración con voz IA sobre
video de apoyo generado con IA, subtítulos karaoke y música de fondo. Encadenable
de punta a punta con `pipeline.py` para correr desatendido (cron o GitHub Actions).

Hermano de [`video-scout-pipeline`](https://github.com/TheRR-ctrl/video-scout-pipeline)
(historias de Reddit narradas para Shorts): comparte la misma arquitectura de
4 etapas, pero reemplaza la fuente de contenido y los motores de voz/video:

| | video-scout-pipeline | video_generation (este repo) |
|---|---|---|
| Fuente de temas | Reddit (RSS público) | Plan de contenido generado con IA, inspirado en canales de referencia de YouTube (RSS público) |
| Voz | edge-tts (gratis, local) | edge-tts o Gemini TTS, configurable |
| Video de apoyo | Clips propios cortados al azar | Un clip por plano, con el motor que elija `motor_broll` (ver abajo) |
| Formato | Shorts verticales | Configurable (`formato`); hoy también shorts verticales |

## Cómo funciona

1. **`content_planner.py`** — lee los títulos recientes de los canales de
   referencia configurados (`reference_channels.py`, vía el feed RSS público
   de YouTube, sin API key) y le pide a Gemini un plan de contenido de
   `dias_plan_contenido` días: tema, título/hook, ángulo psicológico y resumen
   por día. Los títulos de referencia se usan solo como ejemplo de tono — el
   modelo tiene instrucción explícita de generar ideas originales, no copiarlas.
   Salida: `pipeline_state/plan_contenido.json`.
2. **`script_writer.py`** — convierte cada día pendiente del plan en un guion
   completo dividido en escenas (guion + prompt visual por escena). El estilo
   del prompt visual **depende de `motor_broll`**: descripción filmable en
   inglés para Veo ("cinematic close-up, morning light"), concepto a
   visualizar en español para los motores que dibujan con código
   (hyperframes/manim), consulta de búsqueda para los motores de banco (fotos/videos), o
   `[arquetipo] ...` para los motores de identidad fija (estoico,
   curiosidades). Esto importa: si se le pide una toma fotorrealista a un
   motor de motion graphics, el resultado son formas abstractas con rótulos
   decorativos inventados que no tienen relación con la narración. Al cambiar
   de motor hay que regenerar el guion (input `regenerar_guion` del workflow).
   Salida: `guion.txt`.
3. **`generar_video_maestro.py`** — por cada escena: genera la locución
   (`tts_edge.py` o `tts_gemini.py` según `motor_tts`), genera/recicla un clip
   de video de apoyo por plano según `motor_broll` (ver abajo), ajusta el clip
   a la duración real del audio, arma subtítulos karaoke y mezcla música de
   fondo. Concatena todas las escenas del día en un video final con ffmpeg.
   Salida: `pipeline_state/resultado_lote.json`.
4. **`publisher.py`** — chequeo técnico + chequeo de contenido (Gemini), sube
   el video a YouTube como **privado**, programado para publicarse solo tras
   una ventana de revisión manual. La descripción siempre declara que el
   guion, la voz y el video de apoyo están generados con IA.
5. **`pipeline.py`** — orquesta las 4 etapas en una sola corrida.

Cada etapa es incremental: el plan, el guion y los videos ya generados se
reutilizan/omiten, así que una corrida interrumpida retoma donde quedó.

## Setup

```bash
pip install -r requirements.txt
```

`config.json` ya está commiteado en el repo con valores por defecto (no
contiene secretos, solo canales de referencia, voces, duraciones, etc. —
las credenciales van aparte, por variable de entorno o archivo gitignoreado).
Ajusta ahí al menos `canales_referencia` (URLs de YouTube de canales
similares al que quieres crear) y `carpeta_salida`. Nunca commitees
`client_secret.json` ni `youtube_token.json` — ver `.gitignore`.

**Desarrollo local:** copia `.env.example` a `.env` y completa tus valores
ahí — cada script lo carga automáticamente (`env_local.py`) antes de leer
`os.environ`, así no hace falta exportar variables a mano en cada sesión de
terminal. `.env` nunca se commitea (ver `.gitignore`). En GitHub Actions no
existe `.env`: las mismas variables llegan como Secrets del repo (ver más
abajo), así que el mismo código funciona en ambos lugares sin cambios.

Variables de entorno:

- `GEMINI_API_KEY` — gratis en https://aistudio.google.com/apikey. La usan
  `content_planner.py`, `script_writer.py`, `tts_gemini.py`, `veo_broll.py` y
  `publisher.py`. **Ojo:** la generación de video con Veo consume cuota de
  pago más rápido que las llamadas de texto/voz — revisa los límites de tu
  cuenta antes de correr `pipeline.py` sin supervisión.
- `PEXELS_API_KEY` — gratis y sin tarjeta en https://www.pexels.com/api/.
  Hace falta con `motor_broll` en `fotos`, `videos` o `estoico`, que son los
  que bajan material de banco (`fondos_stock.py` / `videos_stock.py`).
- `PIXABAY_API_KEY` — **opcional**, gratis y sin tarjeta en
  https://pixabay.com/api/docs/. Es la segunda fuente de video de
  `motor_broll=videos`, con cuota independiente de la de Pexels. Sin ella el
  motor funciona igual, solo con menos catálogo donde elegir.
- `JAMENDO_CLIENT_ID` — opcional, solo para `actualizar_musica.py` (música de
  fondo). Gratis en https://devportal.jamendo.com/.

Los nombres de modelo (`modelo_texto`, `modelo_tts`, `modelo_veo`,
`modelo_revision` en `config.json`) son configurables porque los IDs de
modelo de Gemini cambian con el tiempo — confirma los vigentes en
[ai.google.dev](https://ai.google.dev/gemini-api/docs/models) antes de correr
el pipeline por primera vez.

## Control de gasto (`presupuesto.py`)

Tope **diario** y duro sobre las tres cosas que consumen cuota de pago: las
llamadas de texto a Gemini, el TTS de Gemini y los clips de Veo. Los tres
puntos de gasto pasan por `presupuesto.consumir()` antes de llamar a la API;
si el día ya llegó al tope, la llamada no se hace y la etapa falla con un
mensaje claro en vez de gastar.

El contador vive en `pipeline_state/gasto_<fecha>.json`, en disco y no en
memoria, a propósito: el workflow se relanza varias veces por día durante las
pruebas, y un contador por proceso se reiniciaría en cada corrida sin frenar
nada.

Para que se genere un solo clip de Veo hacen falta **dos** permisos
independientes, y ninguno se activa por descuido:

1. `modo_pruebas: false` en `config.json` (viene en `true`, y mientras lo esté
   fuerza el tope de video a cero pase lo que pase).
2. `PERMITIR_VEO=1` en el entorno.

Los topes se configuran en `config.json` con `max_llamadas_texto_por_dia`,
`max_llamadas_tts_por_dia` y `max_clips_veo_por_dia`.

Además, `pipeline.py` tiene un freno de sobreproducción: si hay
`UMBRAL_BACKLOG_VIDEOS` (5) videos ya renderizados esperando publicación, se
saltan las etapas de plan y guion, porque seguir generando solo acumula un
colchón que no se alcanza a publicar. `--forzar` lo ignora.

## Formatos de canal (`formato_canal` en config.json)

Un `formato_canal` es un paquete con nombre que fija de una sola vez el motor
de b-roll, el de voz, la carpeta de salida y los hashtags (`formatos_canal.py`),
además del tono de guion que usan `content_planner.py` y `script_writer.py`.
Existe porque probar un estilo distinto implicaba acordarse a mano de cuatro
parámetros sueltos a la vez, y porque sin carpetas separadas dos estilos se
pisaban los archivos en el mismo `Videos Creados/`.

| formato_canal | De qué va | motor_broll | Voz |
|---|---|---|---|
| `psicologia` | Explicador analítico con diagramas dibujados por código | `hyperframes` | Femenina, tono cálido |
| `emocional` | Reflexión/poesía sobre fotos reales | `fotos` | Masculina, tono neutro |
| `estoico` | Aforismos sobre dolor y disciplina, con glifo fijo | `estoico` | Masculina, grave |
| `curiosidades` | Curiosidades científicas con gráficos animados | `curiosidades` | Masculina, entusiasta |
| `manual` (default) | No toca nada: mandan los campos sueltos de config.json | — | — |

Cada formato tiene su guion semilla escrito a mano (`guion.semilla.txt` para
psicología, `guion.semilla.<formato>.txt` para los demás), para probar el
render completo sin gastar una sola llamada a Gemini: inputs
`usar_guion_semilla` + `archivo_guion_semilla` del workflow, con
`desde_etapa=video`.

Al cambiar de formato hay que regenerar el plan y el guion
(`regenerar_plan`), porque el guion viejo trae los prompts visuales con el
formato del motor anterior.

## Motor de video de apoyo (`motor_broll` en config.json)

Seis motores. Los cinco primeros son gratis; `veo` es el único que cuesta.
El default efectivo es `"hyperframes"` (lo fija `config.json`, y el workflow
lo repite como default de su propio input).

- `"veo"` — Gemini Veo genera video fotorrealista por escena, de
  pago y lento (minutos por clip). Además del costo, exige dos permisos
  independientes para llegar a correr: `modo_pruebas: false` en config.json
  **y** `PERMITIR_VEO=1` en el entorno (ver "Control de gasto").
- `"manim"` — `manim_broll.py` le pide a Gemini el *código* de una escena de
  [Manim](https://www.manim.community/) (motion graphics: líneas, formas,
  texto, estilo grid neón sobre fondo oscuro) y la renderiza localmente.
  Gratis, determinista (mismo prompt → mismo resultado, cacheado en
  `pipeline_state/manim_cache/`) e ideal para nichos de explicador visual
  con geometría/matemática exacta (órbitas, gráficas, escalas). Requiere
  las dependencias nativas de Manim (Cairo, Pango) — ya instaladas en el
  workflow de GitHub Actions; en local: `apt install libcairo2-dev
  libpango1.0-dev pkg-config` (Debian/Ubuntu) antes de `pip install -r
  requirements.txt`.
- `"hyperframes"` — `hyperframes_broll.py` le pide a Gemini el *HTML* de una
  composición de [HyperFrames](https://github.com/heygen-com/hyperframes)
  (HTML + CSS + GSAP → mp4 vía Chrome headless) y la renderiza localmente
  con `npx hyperframes`. Mismo trato que Manim (gratis, determinista,
  cacheado en `pipeline_state/hyperframes_cache/`), pero mejor para motion
  graphics tipo "anuncio" (texto kinético, transiciones, formas animadas
  con easings declarativos) que para geometría exacta. Usa GSAP vendorizado
  en `vendor/gsap.min.js` (no CDN, para que el render no dependa de red).
  Requiere Node.js 22+ — ya configurado en el workflow de GitHub Actions.

  **Generación por lotes** (`tam_lote_hyperframes`, default 5): en vez de
  una llamada a Gemini por escena, pide el HTML de `tam_lote_hyperframes`
  escenas en una sola llamada (respuesta JSON con un array de composiciones).
  Con un guion de 27 escenas, esto baja de ~27 llamadas de texto a ~6 —
  clave porque el tier gratuito de Gemini limita las solicitudes/día del
  modelo de texto (no solo las de TTS o Veo), y un video completo con el
  motor "de a una por escena" agota esa cuota antes de terminar. Un reintento
  solo vuelve a pedir las escenas que fallaron, no el lote entero.

  **Gráficas de datos (`vendor/chart-story.html`):** cuando una escena es una
  comparación de números (tamaños, distancias, temperaturas...), el prompt de
  sistema le ofrece a Gemini una sub-composición ya construida del catálogo
  oficial de HyperFrames (barras/línea/donut/progreso, valores exactos, sin
  redondear) en vez de dejarlo inventar su propia animación de datos desde
  cero — menos renders rotos por HTML/SVG mal generado, y una gráfica con
  mejor terminado. Vendorizada igual que `gsap.min.js` (CDN reemplazado por
  la copia local) para que siga sin depender de red.
- `"fotos"` — `fondos_stock.py` busca una foto real en
  [Pexels](https://www.pexels.com/api/) (gratis, requiere `PEXELS_API_KEY`) por
  cada plano y le aplica un Ken Burns (zoom/paneo lento) para que no quede una
  imagen congelada. El `VISUAL:` de cada plano es directamente la consulta de
  búsqueda, en español y de 2-4 palabras.
- `"videos"` — `videos_stock.py` hace lo mismo que `fotos` pero contra el
  catálogo de **video** de Pexels y Pixabay: en vez de una foto fija con zoom,
  el plano es metraje real con movimiento propio. Lee el mismo `VISUAL:` que
  `fotos`, así que se puede cambiar de uno al otro sin reescribir el guion. Si
  ninguna de las dos fuentes devuelve nada para la consulta, prueba con
  términos comodín (`naturaleza`, `cielo`, `océano`...) y recién después cae a
  `fondos_stock`, así que en el peor caso da exactamente lo que daba `fotos`.
  Además no repite el mismo clip dos veces dentro del mismo video. Es el motor
  recomendado para el formato emocional.
- `"estoico"` — `estoico_broll.py` compone dos capas: un **glifo animado por
  código** (`plantillas_sello.py`: grieta, brasa, circulo, anillos, ascenso) y
  una foto de Pexels debajo. El glifo se dibuja en blanco/dorado sobre negro
  puro y se superpone usando su propia luminancia como canal alfa. Da una
  identidad visual fija y reconocible —el mismo repertorio de glifos en todos
  los videos— sin pagar por generar una imagen distinta por escena. El
  `VISUAL:` va como `[arquetipo] palabras de la foto`.
- `"curiosidades"` — `curiosidades_broll.py` dibuja el cuadro entero por código
  con `plantillas_curiosidades.py`: cuatro arquetipos de gráfico (`rayo`,
  `barra`, `onda`, `ruido`) sobre una estética fija de laboratorio nocturno,
  con lluvia ambiental y reflejo de agua. No usa fotos ni API de imagen, así
  que es el único motor visual que no necesita ninguna credencial. El `VISUAL:`
  va como `[arquetipo] descripción. Etiquetas: a, b. Datos: n, n`.

Los tres motores que dibujan con HyperFrames (`hyperframes`, `estoico` y
`curiosidades`) comparten el mismo render: `hyperframes_broll.renderizar_html()`.

El workflow de GitHub Actions (`.github/workflows/pipeline.yml`) expone
`motor_broll` como input de `workflow_dispatch`, así que se puede elegir
sin tocar código: pestaña **Actions** → *Pipeline de contenido* → **Run
workflow** (funciona igual desde la app móvil de GitHub — un par de toques,
sin terminal).

**Caché entre corridas:** cada corrida del workflow arranca de un checkout
limpio, así que sin ayuda `pipeline_state/` (los clips ya generados) y
`guion.txt` se perderían al terminar el job — si una corrida se queda sin
cuota gratuita a mitad de un video, la siguiente empezaría de cero y
volvería a gastar cuota en escenas que ya habían salido bien. El workflow
usa `actions/cache/restore` + `actions/cache/save` (por separado, no la
acción combinada `actions/cache`) para persistir `pipeline_state/`,
`guion.txt` y `Videos Creados/` entre corridas — clave por `run_id` +
`restore-keys` para recuperar siempre la más reciente. Importante: el paso
de guardado usa `if: always()` a propósito, porque la acción combinada
`actions/cache` solo guarda cuando el job termina en éxito (`post-if:
success()` en su definición) — como este pipeline casi siempre "falla" al
toparse con la cuota a mitad de un video, con la acción combinada nunca se
guardaría nada del progreso real ya hecho.

## Motor de narración (`motor_tts` en config.json)

- `"gemini"` (default) — `tts_gemini.py`. La capa gratuita tiene un límite
  duro de **10 solicitudes/día por proyecto** — no alcanza para un solo
  video de guion largo (20+ escenas), y una suscripción de consumidor tipo
  Google One/Gemini Advanced **no** sube ese límite (solo lo hace habilitar
  facturación de pago por uso en el proyecto de la API key).
- `"edge"` — `tts_edge.py`, usa [edge-tts](https://github.com/rany2/edge-tts)
  (voces neuronales de Microsoft Edge). Gratis, sin límite diario, mismo
  motor que ya usa el pipeline hermano `video-scout-pipeline`. Voces por
  defecto: `voz_masculina_edge` / `voz_femenina_edge` en config.json
  (`es-MX-JorgeNeural` / `es-MX-DaliaNeural`).

Si el pipeline te está topando con 429 `RESOURCE_EXHAUSTED` en la etapa de
locución, cambiá `motor_tts` a `"edge"`.

**Subtítulos:** con `"edge"` los subtítulos salen de `--write-subtitles`, o sea
con las marcas de tiempo **reales por palabra** que devuelve el propio motor de
voz (igual que en `video-scout-pipeline`). Con `"gemini"` no hay timings
disponibles, así que se cae a repartir la duración medida del audio
proporcionalmente a los caracteres de cada bloque — funciona, pero se desfasa
apenas la locución cambia de ritmo. Es una razón más para preferir `"edge"`.
El tamaño de bloque en pantalla es `PALABRAS_POR_SUBTITULO` (4): con menos, el
karaoke parpadea palabra por palabra y no se alcanza a leer.

## Mezcla de música con ducking nativo (`hyperframes_audio_mix.py`)

La narración y la música de fondo del día se mezclan con el **voiceover
carve** de HyperFrames (skill `hyperframes-audio`) en vez de una mezcla
estática de ffmpeg. La diferencia:

- **Antes:** volumen fijo de música al 8% durante todo el video
  (`amix` + `volume=0.08`), sin importar si hay narración en ese instante o
  no — la música pierde presencia todo el tiempo, la haya o no.
- **Ahora:** el carve analiza en qué bandas de frecuencia y en qué momentos
  exactos hay voz, y solo recorta esas bandas de la música justo ahí. La
  música conserva sus graves y agudos y sigue sonando como música, incluso
  con narración encima — y vuelve a su volumen normal en los silencios.

Implementación (`hyperframes_audio_mix.py`): arma una composición mínima de
HyperFrames con la narración del día como voz y la música (ya recortada a la
duración exacta y con fade-out) como "bed", corre `vendor/carve.mjs`
(vendorizado del propio HyperFrames) para que escriba la cadena de EQ +
automatización, renderiza esa composición (video descartable, solo importa
el audio) y extrae la pista mezclada con ffmpeg. Requiere `@hyperframes/core`
instalado vía `npm ci` (ver `package.json`) — si Node/npm/el carve fallan por
lo que sea, el pipeline cae automáticamente a la mezcla estática de siempre
(`ducking_hyperframes: false` en config.json la desactiva a propósito).
`fuerza_carve_musica` (default 0.3, rango 0-1) controla qué tan agresivo es
el recorte — más alto en videos donde la música es más protagonista.

## Música de fondo (`actualizar_musica.py`)

Igual que en `video-scout-pipeline`: descarga pistas royalty-free de Jamendo
por tono (`reflexivo`, `inspirador`, `tenso`, `esperanzador`) como
`musica_<tono>_<artista>_<id>.mp3`. No es parte de la corrida diaria — se
corre manualmente o con su propio cron cada tanto. Atribución guardada en
`pipeline_state/musica_atribucion.json` y créditada automáticamente en la
descripción del video por `publisher.py`.

## Publicación en YouTube

Para subir a YouTube necesitas un OAuth "Desktop app" client de Google Cloud
Console guardado como `client_secret.json`, y correr una vez
`generar_youtube_token.py` (abre un link de autorización, guarda
`youtube_token.json`, que luego se renueva solo).

Recuerda poner el consent screen de tu proyecto de Google Cloud en estado
**"In production"** (sin necesidad de verificación completa) — si se queda en
"Testing", el refresh token expira a los 7 días y rompe una corrida
desatendida cada semana.

## Corriendo el pipeline

```bash
python pipeline.py                # corre las 4 etapas
python pipeline.py --hasta guion  # solo plan + guion (sin renderizar/publicar)
python pipeline.py --desde video  # solo video + publicar (guion.txt ya debe existir)
```

## Automatizando (cron / GitHub Actions)

`.github/workflows/pipeline.yml` corre el pipeline en GitHub Actions. A
diferencia de `video-scout-pipeline`, **no corre en cron por defecto**: la
generación de video con Veo tiene costo y toma minutos por escena, así que
hasta que confirmes cuánto tarda/cuesta un día real, se lanza a mano desde
la pestaña *Actions* → *Pipeline de contenido* → *Run workflow* (puedes
elegir hasta qué etapa correr). Cuando quieras automatizarlo del todo,
descomenta el bloque `schedule` del workflow.

Necesita los mismos tres secrets que `video-scout-pipeline`
(`Settings` → `Secrets and variables` → `Actions`):

- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_SECRET` — contenido completo de `client_secret.json`
- `YOUTUBE_TOKEN` — contenido completo de `youtube_token.json`
