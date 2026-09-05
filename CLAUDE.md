# video_generation

Pipeline de contenido para un canal de YouTube en español (ver README.md para
la arquitectura completa de las 4 etapas). Motor de b-roll gratuito principal:
HyperFrames (`hyperframes_broll.py`) + edge-tts (`tts_edge.py`).

## Preferencia permanente del usuario

Cuando explores o trabajes con una herramienta/librería nueva (HyperFrames,
Manim, etc.) y descubras una capacidad que podría aplicar a este proyecto o a
sus hermanos (`video-scout-pipeline`) aunque no se haya pedido explícitamente,
avísale al usuario en el momento en que la descubras — no la guardes para
después ni la omitas por no ser parte de la tarea actual. El usuario prefiere
enterarse de estas oportunidades activamente en vez de tener que preguntarlas.

Ejemplos de esto ya hechos: el catálogo de componentes de HyperFrames
(gráficas/mapas animados vía `hyperframes add`) y la mezcla de audio nativa
de HyperFrames (voiceover carve / ducking automático) en vez de mezclar con
ffmpeg después del render.

## Referencia de nicho: flujo de "videos largos con IA" (a implementar)

El usuario aportó la transcripción de *"Nuevo Nicho: Crea Videos Largos 100%
GRATIS e ILIMITADOS"* (Tutoriales Informáticos + IA,
https://www.youtube.com/watch?v=mBzOQDufStc) para incorporar sus técnicas a
nuestros videos. El flujo del video es manual (ChatGPT + DeepSeek + Google Flow
+ Filmora); lo que importa acá no son esas herramientas —nuestro pipeline ya
está automatizado y es mejor en eso— sino las decisiones de formato que
explican por qué ese nicho funciona. Pendientes de implementar, en orden de
impacto:

1. **Títulos como pregunta abierta sobre lo cotidiano.** El canal de referencia
   llegó a 160.000 suscriptores con 21 videos usando títulos del tipo "¿Qué
   hacían nuestros antepasados todo el día?", "¿Qué soñaban los primeros
   humanos?". Va en `content_planner.py`: pregunta que despierte curiosidad, no
   enunciado.
2. **Muchas más imágenes por minuto.** Ese flujo genera ~150 imágenes para un
   video, o sea un cambio visual cada 3-5 segundos, con una imagen por línea de
   guion. Nuestras escenas duran 15-25s con un solo clip: es la diferencia de
   ritmo más grande entre los dos formatos. Habría que subdividir la escena en
   varios planos visuales manteniendo una sola locución.
3. **Piso de palabras en el prompt visual.** Ese flujo exige mínimo 120 palabras
   por prompt de imagen, y ese es el motivo de que las imágenes salgan
   elaboradas. Nuestros `prompt_visual` son de una o dos frases: conviene
   probar un piso explícito en `script_writer.py`.
4. **Miniatura, descripción y hashtags generados junto con el guion**, no
   después ni a mano. `publisher.py` sube sin miniatura propia.
5. **Detección de silencios en la locución**, para quitar los espacios muertos
   antes de montar.
6. **Locución levemente acelerada** (ellos suben la velocidad del TTS): da un
   ritmo más ágil sin sonar antinatural.

Ya cubierto por nuestro pipeline: todo texto en pantalla en español, formato
horizontal/vertical configurable, y la generación automática del guion por
escenas con su duración.
