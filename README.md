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
| Voz | edge-tts (gratis, local) | Gemini TTS |
| Video de apoyo | Clips propios cortados al azar | Clips generados por escena con Gemini Veo |
| Formato | Shorts verticales | Video largo horizontal |

## Cómo funciona

1. **`content_planner.py`** — lee los títulos recientes de los canales de
   referencia configurados (`reference_channels.py`, vía el feed RSS público
   de YouTube, sin API key) y le pide a Gemini un plan de contenido de
   `dias_plan_contenido` días: tema, título/hook, ángulo psicológico y resumen
   por día. Los títulos de referencia se usan solo como ejemplo de tono — el
   modelo tiene instrucción explícita de generar ideas originales, no copiarlas.
   Salida: `pipeline_state/plan_contenido.json`.
2. **`script_writer.py`** — convierte cada día pendiente del plan en un guion
   completo dividido en escenas (guion + prompt visual en inglés por escena,
   para el clip de Veo de esa escena). Salida: `guion.txt`.
3. **`generar_video_maestro.py`** — por cada escena: genera la locución
   (`tts_gemini.py`), genera/recicla el clip de video de apoyo
   (`veo_broll.py`, con caché por prompt en `pipeline_state/veo_cache/`),
   ajusta el clip a la duración real del audio, arma subtítulos karaoke y
   mezcla música de fondo. Concatena todas las escenas del día en un video
   final con ffmpeg. Salida: `pipeline_state/resultado_lote.json`.
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

Copia `config.example.json` a `config.json` y ajusta al menos
`canales_referencia` (URLs de YouTube de canales similares al que quieres
crear) y `carpeta_salida`. Nunca commitees `config.json`, `client_secret.json`
ni `youtube_token.json` — ver `.gitignore`.

Variables de entorno:

- `GEMINI_API_KEY` — gratis en https://aistudio.google.com/apikey. La usan
  `content_planner.py`, `script_writer.py`, `tts_gemini.py`, `veo_broll.py` y
  `publisher.py`. **Ojo:** la generación de video con Veo consume cuota de
  pago más rápido que las llamadas de texto/voz — revisa los límites de tu
  cuenta antes de correr `pipeline.py` sin supervisión.
- `JAMENDO_CLIENT_ID` — opcional, solo para `actualizar_musica.py` (música de
  fondo). Gratis en https://devportal.jamendo.com/.

Los nombres de modelo (`modelo_texto`, `modelo_tts`, `modelo_veo`,
`modelo_revision` en `config.json`) son configurables porque los IDs de
modelo de Gemini cambian con el tiempo — confirma los vigentes en
[ai.google.dev](https://ai.google.dev/gemini-api/docs/models) antes de correr
el pipeline por primera vez.

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

Igual que en `video-scout-pipeline`: `python pipeline.py` en un cron/Tarea
Programada, o un workflow de GitHub Actions con los secrets
`YOUTUBE_CLIENT_SECRET`, `YOUTUBE_TOKEN` y `GEMINI_API_KEY`. Ten en cuenta que
la generación de video con Veo es más lenta que renderizar con clips
propios — ajusta el timeout del job en consecuencia.
