"""
Gemini Utils — reintentos compartidos para llamadas a la API de Gemini.

La capa gratuita de Gemini tiene límites de cuota (por minuto y por día,
por proyecto y por modelo). Cuando se excede, la API devuelve 429
RESOURCE_EXHAUSTED con un 'retryDelay' sugerido en el propio mensaje de
error. Este helper reintenta respetando ese valor cuando está presente, con
backoff exponencial como respaldo si no lo trae.

No hay forma de distinguir por código si un 429 es por cuota-por-minuto
(se resuelve solo en segundos) o cuota-por-día (no se resuelve hasta el
día siguiente); reintentar unas pocas veces cubre el primer caso sin
bloquear indefinidamente el segundo.
"""
import re
import time
import logging

from google.genai import errors as genai_errors

logger = logging.getLogger("gemini_utils")

_RE_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _extraer_retry_delay(exc, por_defecto):
    m = _RE_RETRY_DELAY.search(str(exc))
    return float(m.group(1)) + 1.0 if m else por_defecto


def llamar_con_reintentos(fn, *args, reintentos=3, espera_base_seg=20.0, **kwargs):
    """Llama a fn(*args, **kwargs) reintentando en 429 (RESOURCE_EXHAUSTED).
    Cualquier otro error de API se propaga de inmediato (no tiene sentido
    reintentar un 400 o 403)."""
    for intento in range(1, reintentos + 1):
        try:
            return fn(*args, **kwargs)
        except genai_errors.APIError as exc:
            es_cuota = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
            if not es_cuota or intento == reintentos:
                raise
            espera = _extraer_retry_delay(exc, espera_base_seg * intento)
            logger.warning(
                f"Cuota excedida (intento {intento}/{reintentos}), "
                f"reintentando en {espera:.0f}s..."
            )
            time.sleep(espera)
    return None  # inalcanzable: el último intento siempre retorna o lanza
