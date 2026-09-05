"""
TTS Edge — genera narración con edge-tts (voces neuronales de Microsoft
Edge, gratis y sin límite diario de cuota), como alternativa a tts_gemini.py.

Existe porque la capa gratuita de Gemini TTS tiene un límite duro de 10
solicitudes/día por proyecto — insuficiente para un solo video de guion
largo (20+ escenas). edge-tts no tiene ese techo. Es el mismo motor que ya
usa el pipeline hermano video-scout-pipeline (ver ese repo para el patrón
original), invocado igual acá: como subproceso CLI (`python -m edge_tts`)
en vez de una librería async, para no meter un runtime asyncio dentro de
este pipeline que es sincrónico.

A diferencia de Gemini TTS, edge-tts devuelve subtítulos con marcas de tiempo
reales por palabra (--write-subtitles): generar_audio los escribe si se le pasa
ruta_srt_salida. El pipeline los usa cuando están disponibles y solo cae al
timing estimado por caracteres con el motor de Gemini, que no los ofrece.

Requiere: pip install edge-tts
No requiere credenciales (habla directo con el servicio de Microsoft Edge).
"""
import os
import time
import logging
import subprocess
import sys
import tempfile

MODELO_DEFAULT = None  # sin uso; existe solo por paridad de firma con tts_gemini
VOZ_FALLBACK_MASCULINA = "es-MX-JorgeNeural"
VOZ_FALLBACK_FEMENINA = "es-MX-DaliaNeural"
TIMEOUT_SEG = 60
# Velocidad de la locución. El pipeline hermano narra siempre entre +15% y +20%
# y ese es el ritmo del formato: a velocidad normal la voz suena a lectura y en
# un short el espectador desliza antes de que termine la frase.
VELOCIDAD_DEFAULT = "+18%"
TONO_DEFAULT = "+0Hz"
# Palabras por segundo mínimas que se consideran plausibles. edge-tts a veces
# devuelve código 0 y un mp3 válido pero truncado —la mitad de la frase—, y sin
# esta comprobación ese audio a medias se da por bueno y desincroniza la escena.
# El umbral es holgado a propósito (6 palabras/s es más rápido de lo que habla
# cualquier voz) para no descartar tomas buenas.
PALABRAS_POR_SEGUNDO_MAX = 6.0

logger = logging.getLogger("tts_edge")


def _voz_fallback(voz):
    """edge-tts no acepta nombres de voz de Gemini (Charon, Kore, ...): si
    'voz' no es un nombre de voz de edge-tts reconocible (formato
    'xx-XX-NombreNeural'), usa un fallback fijo en vez de fallar."""
    if voz and voz.count("-") >= 2 and voz.endswith("Neural"):
        return voz
    return VOZ_FALLBACK_MASCULINA


def _duracion_media(ruta):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", ruta],
            capture_output=True, text=True, timeout=30,
        )
        return float((res.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _audio_completo(ruta_audio, texto):
    """False si el audio es demasiado corto para el texto que debía narrar.

    Es la red que atrapa las tomas truncadas: sin ella, un mp3 con la mitad de
    la frase pasa como válido (existe y pesa más de cero) y la escena queda
    desincronizada sin que nada lo reporte."""
    palabras = len(texto.split())
    if not palabras:
        return True
    duracion = _duracion_media(ruta_audio)
    return duracion >= palabras / PALABRAS_POR_SEGUNDO_MAX


def generar_audio(texto, voz, ruta_audio_salida, modelo=MODELO_DEFAULT, reintentos=3,
                  ruta_srt_salida=None, velocidad=VELOCIDAD_DEFAULT, tono=TONO_DEFAULT):
    """Misma interfaz que tts_gemini.generar_audio: genera narración TTS y
    la guarda en ruta_audio_salida (mp3). Devuelve True/False; nunca reporta
    éxito si el archivo resultante no es válido ni si quedó truncado.

    Si se pasa ruta_srt_salida, además escribe ahí los subtítulos con marcas de
    tiempo REALES por palabra que devuelve edge-tts (--write-subtitles). Eso es
    lo que permite que el karaoke siga a la voz de verdad, en vez de repartir el
    tiempo de cada escena proporcionalmente a los caracteres (ver
    generar_video_maestro.py). tts_gemini no puede hacer esto: es la ventaja
    concreta de este motor.

    `velocidad` y `tono` son los de edge-tts (--rate/--pitch): la velocidad da
    el ritmo del formato corto y el tono, variado por video, evita que todos los
    videos del canal suenen a la misma voz robótica."""
    voz_efectiva = _voz_fallback(voz)

    for intento in range(1, reintentos + 1):
        try:
            voz_intento = voz_efectiva if intento < reintentos else VOZ_FALLBACK_MASCULINA
            # Restos de un intento anterior: si el reintento falla antes de
            # escribir, un archivo viejo a medias se daría por bueno.
            for ruta in (ruta_audio_salida, ruta_srt_salida):
                if ruta and os.path.exists(ruta):
                    os.remove(ruta)

            # El texto va por archivo y no como argumento: una narración con
            # comillas, guiones largos o saltos de línea rompe el paso por
            # línea de comandos según el shell.
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as f:
                ruta_texto = f.name
                f.write(texto)
            try:
                comando = [sys.executable, "-m", "edge_tts",
                           f"--rate={velocidad}", f"--pitch={tono}",
                           "--file", ruta_texto, "--voice", voz_intento,
                           "--write-media", ruta_audio_salida]
                if ruta_srt_salida:
                    comando += ["--write-subtitles", ruta_srt_salida]
                res = subprocess.run(
                    comando, capture_output=True, text=True, timeout=TIMEOUT_SEG,
                )
            finally:
                os.remove(ruta_texto)

            if res.returncode == 0 and os.path.isfile(ruta_audio_salida) and os.path.getsize(ruta_audio_salida) > 0:
                if _audio_completo(ruta_audio_salida, texto):
                    return True
                logger.warning(
                    f"edge-tts intento {intento}/{reintentos}: el audio quedó "
                    f"demasiado corto para {len(texto.split())} palabras, se descarta."
                )
            else:
                logger.warning(f"edge-tts intento {intento}/{reintentos}: {(res.stderr or '').strip()[-500:]}")
        except Exception as exc:
            logger.warning(f"edge-tts intento {intento}/{reintentos} falló: {exc}")

        if intento < reintentos:
            time.sleep(2 * intento)

    return False
