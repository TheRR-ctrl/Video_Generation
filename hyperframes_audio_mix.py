"""
Mezcla de narración + música de fondo usando el "voiceover carve" nativo de
HyperFrames (ver https://hyperframes.heygen.com, skill hyperframes-audio) en
vez de la mezcla estática de ffmpeg (volumen fijo de música + amix).

El carve analiza en qué bandas de frecuencia y en qué momentos hay voz, y
solo recorta esas bandas en la música exactamente ahí — al contrario de bajar
el volumen de toda la pista durante todo el video, que es lo que hacía la
mezcla anterior. La música mantiene sus graves y agudos, y sigue sonando como
música incluso mientras hay narración encima.

Cómo funciona:
1. Arma una composición mínima de HyperFrames (sin animación, solo dos
   <audio>: la narración como voz y la música como "bed").
2. Corre `vendor/carve.mjs` (vendorizado del propio HyperFrames, requiere
   @hyperframes/core instalado vía package.json/npm) para que analice el par
   y escriba en la composición la cadena de EQ + automatización que hace el
   ducking.
3. Renderiza esa composición con `npx hyperframes render` (video descartable
   a resolución mínima — solo importa el audio) y extrae la pista de audio
   resultante con ffmpeg.

Si cualquier paso falla (Node/ffmpeg no disponibles, timeout, etc.),
`mezclar_narracion_musica` devuelve False y el llamador debe caer de vuelta a
la mezcla estática de ffmpeg — esto es una mejora de calidad, nunca debe
tumbar una corrida del pipeline.
"""
import os
import shutil
import logging
import tempfile
import subprocess

import hyperframes_broll

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CARVE_SCRIPT = os.path.join(BASE_DIR, "vendor", "carve.mjs")

# 0.25 es el default de HyperFrames (recorte de 6dB en tres bandas). Un poco
# más fuerte porque la narración es el contenido principal del video, no un
# acompañamiento sobre música.
FUERZA_CARVE_DEFAULT = 0.3

# El video de esta composición es descartable (solo nos importa el audio),
# así que se renderiza a resolución mínima y calidad "draft" para que el
# costo sea proporcional a lo que realmente aprovechamos.
ANCHO_LIENZO = 320
ALTO_LIENZO = 180

# Generoso: aunque el render es de un lienzo mínimo, la duración es la del
# video completo del día (puede pasar de 10 minutos), y el carve además tiene
# que decodificar y analizar toda la narración y la música con ffmpeg.
TIMEOUT_CARVE_SEG = 300
TIMEOUT_RENDER_SEG = 900

logger = logging.getLogger("hyperframes_audio_mix")

_PLANTILLA_HTML = """<!doctype html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <script src="gsap.min.js"></script>
    <style>
      body {{ margin: 0; background: #000; }}
      #root {{ position: relative; width: {ancho}px; height: {alto}px; overflow: hidden; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="{ancho}" data-height="{alto}" data-duration="{duracion}">
      <audio id="narracion" src="narracion{ext_narracion}" data-start="0" data-duration="{duracion}" data-audio-group="voiceover"></audio>
      <audio id="musica" src="musica{ext_musica}" data-start="0" data-duration="{duracion}"></audio>
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _herramientas_disponibles():
    if not hyperframes_broll._archivo_valido(hyperframes_broll.RUTA_GSAP_VENDOR):
        logger.warning(f"No se encontró {hyperframes_broll.RUTA_GSAP_VENDOR}; se omite el carve.")
        return False
    if not os.path.isfile(RUTA_CARVE_SCRIPT):
        logger.warning(f"No se encontró {RUTA_CARVE_SCRIPT}; se omite el carve.")
        return False
    if shutil.which("node") is None:
        logger.warning("No se encontró 'node' en el PATH; se omite el carve.")
        return False
    return True


def mezclar_narracion_musica(ruta_narracion, ruta_musica, duracion_seg, ruta_audio_salida, fuerza=FUERZA_CARVE_DEFAULT):
    """Mezcla narración + música con ducking nativo de HyperFrames y escribe
    el resultado en ruta_audio_salida. Devuelve True si tuvo éxito, False si
    falló algo (el llamador debe caer de vuelta a la mezcla estática)."""
    if not _herramientas_disponibles():
        return False

    ext_narracion = os.path.splitext(ruta_narracion)[1] or ".m4a"
    ext_musica = os.path.splitext(ruta_musica)[1] or ".mp3"

    try:
        with tempfile.TemporaryDirectory(prefix="hyperframes_audio_mix_") as tmp:
            shutil.copyfile(ruta_narracion, os.path.join(tmp, f"narracion{ext_narracion}"))
            shutil.copyfile(ruta_musica, os.path.join(tmp, f"musica{ext_musica}"))
            shutil.copyfile(hyperframes_broll.RUTA_GSAP_VENDOR, os.path.join(tmp, "gsap.min.js"))

            with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
                f.write(_PLANTILLA_HTML.format(
                    ancho=ANCHO_LIENZO, alto=ALTO_LIENZO, duracion=f"{duracion_seg:.3f}",
                    ext_narracion=ext_narracion, ext_musica=ext_musica,
                ))

            res = subprocess.run(
                ["node", RUTA_CARVE_SCRIPT, "--comp", "index.html",
                 "--bed", "musica", "--voice", "narracion",
                 "--strength", str(fuerza), "--core", BASE_DIR],
                cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_CARVE_SEG,
            )
            if res.returncode != 0:
                logger.warning(f"Voiceover carve falló, se usará mezcla estática: {(res.stderr or res.stdout or '').strip()[-500:]}")
                return False

            env = dict(os.environ)
            env["HYPERFRAMES_SKIP_SKILLS"] = "1"
            env["HYPERFRAMES_TELEMETRY_DISABLED"] = "1"
            res = subprocess.run(
                ["npx", "--yes", f"hyperframes@{hyperframes_broll.VERSION_CLI}", "render",
                 "--quality", "draft", "--fps", "24", "-o", "mezcla.mp4"],
                cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_RENDER_SEG, env=env,
            )
            ruta_mp4 = os.path.join(tmp, "mezcla.mp4")
            if res.returncode != 0 or not hyperframes_broll._archivo_valido(ruta_mp4):
                logger.warning(f"Render de mezcla de audio falló, se usará mezcla estática: {(res.stderr or res.stdout or '').strip()[-500:]}")
                return False

            res = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", ruta_mp4, "-vn", "-c:a", "aac", "-b:a", "192k", ruta_audio_salida],
                capture_output=True, text=True, timeout=120,
            )
            if res.returncode != 0 or not hyperframes_broll._archivo_valido(ruta_audio_salida):
                logger.warning(f"No se pudo extraer el audio de la mezcla: {(res.stderr or '').strip()[-500:]}")
                return False
            return True
    except subprocess.TimeoutExpired:
        logger.warning("Voiceover carve/render de audio superó el timeout, se usará mezcla estática.")
        return False
    except OSError as e:
        logger.warning(f"Error de sistema en el voiceover carve, se usará mezcla estática: {e}")
        return False
