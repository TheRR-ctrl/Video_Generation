"""
Veo B-roll — genera clips de video de apoyo con IA (Gemini Veo) a partir del
prompt_visual de cada escena del guion.

La generación de video es lenta (minutos) y de pago, así que cada clip se
cachea en pipeline_state/veo_cache/ por hash del prompt: si dos escenas (del
mismo día o de días distintos) piden un prompt idéntico, o si una corrida
anterior falló a mitad de camino, no se vuelve a generar ni a cobrar.

Requiere: pip install -U google-genai
Credenciales: GEMINI_API_KEY (gratis/de pago según cuota en
https://aistudio.google.com/apikey).
"""
import os
import time
import hashlib
import logging

from google import genai
from google.genai import types as genai_types

MODELO_DEFAULT = "veo-3.0-generate-001"
CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "veo_cache")
INTERVALO_POLL_SEG = 10
TIMEOUT_SEG = 600

logger = logging.getLogger("veo_broll")

# Veo es lo ÚNICO de este pipeline que cuesta dinero de verdad: HyperFrames y
# Manim dibujan localmente y edge-tts es gratis, así que una corrida entera con
# el motor gratuito gasta unos centavos de texto. Una con Veo genera un clip de
# video por plano — 15 en un short de 5 escenas — y se come el saldo prepago sin
# avisar.
#
# La corrida 34003101637 se disparó sin tocar el campo motor_broll del workflow,
# tomó el default de entonces ("veo"), generó diez clips y murió con 429. Ese
# default ya se cambió, pero un default no alcanza: cualquier config.json viejo,
# cualquier disparo por API y cualquier copia del repo vuelven a poder gastar sin
# que nadie lo haya pedido explícitamente. Por eso el gasto pasa a exigir una
# variable de entorno, que es lo único que no se activa por descuido.
VAR_PERMISO = "PERMITIR_VEO"


def _verificar_permiso():
    if os.environ.get(VAR_PERMISO, "").strip().lower() not in ("1", "true", "si", "sí", "yes"):
        raise RuntimeError(
            f"Veo genera video de pago y está bloqueado: falta {VAR_PERMISO}=1 en el entorno. "
            f"El motor gratuito es 'hyperframes' (motor_broll en config.json o en el workflow). "
            f"Si de verdad querés gastar saldo de Gemini, exportá {VAR_PERMISO}=1."
        )


_client = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _ruta_cache(prompt_visual, aspecto):
    clave = hashlib.sha256(f"{aspecto}|{prompt_visual}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"veo_{clave}.mp4")


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _generar_clip(cliente, prompt_visual, aspecto, modelo, ruta_salida):
    operacion = cliente.models.generate_videos(
        model=modelo,
        prompt=prompt_visual,
        config=genai_types.GenerateVideosConfig(
            aspect_ratio=aspecto,
            number_of_videos=1,
        ),
    )

    esperado = 0.0
    while not operacion.done:
        if esperado >= TIMEOUT_SEG:
            raise TimeoutError(f"Veo no terminó en {TIMEOUT_SEG}s.")
        time.sleep(INTERVALO_POLL_SEG)
        esperado += INTERVALO_POLL_SEG
        operacion = cliente.operations.get(operacion)

    if operacion.error:
        raise RuntimeError(f"Veo devolvió error: {operacion.error}")

    videos_generados = operacion.response.generated_videos
    if not videos_generados:
        raise RuntimeError("Veo no devolvió ningún video.")

    video = videos_generados[0]
    cliente.files.download(file=video.video)
    video.video.save(ruta_salida)


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_DEFAULT, reintentos=2):
    """Devuelve la ruta local a un clip de video para el prompt dado,
    generándolo con Veo si no está ya en caché. None si falló."""
    ruta_salida = _ruta_cache(prompt_visual, aspecto)
    if _archivo_valido(ruta_salida):
        return ruta_salida

    # Se comprueba acá y no al importar: un clip que ya está en caché no cuesta
    # nada y tiene que seguir sirviendo aunque el permiso no esté puesto. Lo que
    # se bloquea es generar uno nuevo, que es lo que cobra.
    _verificar_permiso()

    cliente = _obtener_cliente()
    for intento in range(1, reintentos + 1):
        try:
            _generar_clip(cliente, prompt_visual, aspecto, modelo, ruta_salida)
            if _archivo_valido(ruta_salida):
                return ruta_salida
        except Exception as exc:
            logger.warning(f"Veo intento {intento}/{reintentos} falló: {exc}")
            if intento < reintentos:
                time.sleep(5 * intento)

    return None
