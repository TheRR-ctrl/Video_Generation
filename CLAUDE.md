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
