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

MODELO_DEFAULT = None  # sin uso; existe solo por paridad de firma con tts_gemini
VOZ_FALLBACK_MASCULINA = "es-MX-JorgeNeural"
VOZ_FALLBACK_FEMENINA = "es-MX-DaliaNeural"
TIMEOUT_SEG = 60

logger = logging.getLogger("tts_edge")


def _voz_fallback(voz):
    """edge-tts no acepta nombres de voz de Gemini (Charon, Kore, ...): si
    'voz' no es un nombre de voz de edge-tts reconocible (formato
    'xx-XX-NombreNeural'), usa un fallback fijo en vez de fallar."""
    if voz and voz.count("-") >= 2 and voz.endswith("Neural"):
        return voz
    return VOZ_FALLBACK_MASCULINA


def generar_audio(texto, voz, ruta_audio_salida, modelo=MODELO_DEFAULT, reintentos=3, ruta_srt_salida=None):
    """Misma interfaz que tts_gemini.generar_audio: genera narración TTS y
    la guarda en ruta_audio_salida (mp3). Devuelve True/False; nunca reporta
    éxito si el archivo resultante no es válido.

    Si se pasa ruta_srt_salida, además escribe ahí los subtítulos con marcas de
    tiempo REALES por palabra que devuelve edge-tts (--write-subtitles). Eso es
    lo que permite que el karaoke siga a la voz de verdad, en vez de repartir el
    tiempo de cada escena proporcionalmente a los caracteres (ver
    generar_video_maestro.py). tts_gemini no puede hacer esto: es la ventaja
    concreta de este motor."""
    voz_efectiva = _voz_fallback(voz)

    for intento in range(1, reintentos + 1):
        try:
            voz_intento = voz_efectiva if intento < reintentos else VOZ_FALLBACK_MASCULINA
            comando = [sys.executable, "-m", "edge_tts",
                       "--text", texto, "--voice", voz_intento,
                       "--write-media", ruta_audio_salida]
            if ruta_srt_salida:
                comando += ["--write-subtitles", ruta_srt_salida]
            res = subprocess.run(
                comando, capture_output=True, text=True, timeout=TIMEOUT_SEG,
            )
            if res.returncode == 0 and os.path.isfile(ruta_audio_salida) and os.path.getsize(ruta_audio_salida) > 0:
                return True
            logger.warning(f"edge-tts intento {intento}/{reintentos}: {(res.stderr or '').strip()[-500:]}")
        except Exception as exc:
            logger.warning(f"edge-tts intento {intento}/{reintentos} falló: {exc}")

        if intento < reintentos:
            time.sleep(2 * intento)

    return False
