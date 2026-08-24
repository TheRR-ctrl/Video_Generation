"""
TTS Gemini — genera narración con la API de voz (text-to-speech) de Gemini.

A diferencia de edge-tts (usado en el pipeline hermano video-scout-pipeline),
la API de Gemini TTS no devuelve subtítulos con marcas de tiempo por
palabra. Para las palabras karaoke, generar_video_maestro.py distribuye el
tiempo de cada palabra proporcionalmente a su longitud dentro de la
duración medida del audio de cada escena — una aproximación razonable ya
que no hay pausas largas dentro de una escena de 15-25s.

Requiere: pip install -U google-genai
Credenciales: GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey).
"""
import os
import time
import wave
import logging

from google import genai
from google.genai import types as genai_types

MODELO_DEFAULT = "gemini-2.5-flash-preview-tts"
SAMPLE_RATE_HZ = 24000  # Formato fijo de salida de la API de Gemini TTS: PCM 16-bit mono 24kHz.
SAMPLE_WIDTH_BYTES = 2
CANALES = 1

logger = logging.getLogger("tts_gemini")

_client = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _pcm_a_wav(pcm_bytes, ruta_wav):
    with wave.open(ruta_wav, "wb") as wf:
        wf.setnchannels(CANALES)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(SAMPLE_RATE_HZ)
        wf.writeframes(pcm_bytes)


def generar_audio(texto, voz, ruta_wav_salida, modelo=MODELO_DEFAULT, reintentos=3):
    """Genera narración TTS y la guarda como WAV. Devuelve True/False; nunca
    reporta éxito si el archivo resultante no es válido."""
    cliente = _obtener_cliente()

    for intento in range(1, reintentos + 1):
        try:
            respuesta = cliente.models.generate_content(
                model=modelo,
                contents=texto,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai_types.SpeechConfig(
                        voice_config=genai_types.VoiceConfig(
                            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=voz)
                        )
                    ),
                ),
            )
            parte = respuesta.candidates[0].content.parts[0]
            pcm_bytes = parte.inline_data.data
            if not pcm_bytes:
                raise RuntimeError("Respuesta de audio vacía.")

            _pcm_a_wav(pcm_bytes, ruta_wav_salida)
            if os.path.isfile(ruta_wav_salida) and os.path.getsize(ruta_wav_salida) > 0:
                return True
        except Exception as exc:
            logger.warning(f"TTS intento {intento}/{reintentos} falló: {exc}")
            if intento < reintentos:
                time.sleep(2 * intento)

    return False
