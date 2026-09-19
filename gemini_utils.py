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

También reintenta los errores transitorios del lado del servidor (503
UNAVAILABLE cuando el modelo está sobrecargado, 500 INTERNAL): no dependen de
nada que hagamos y se resuelven solos, pero sin reintentarlos tumban la corrida
entera — una escena que no consigue su clip hace fallar el día completo.
"""
import re
import time
import logging

from google.genai import errors as genai_errors

import presupuesto

logger = logging.getLogger("gemini_utils")

_RE_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _extraer_retry_delay(exc, por_defecto):
    m = _RE_RETRY_DELAY.search(str(exc))
    return float(m.group(1)) + 1.0 if m else por_defecto


def _clasificar_error(exc):
    """Devuelve 'cuota', 'transitorio' o None (no reintentable) para un error
    de la API."""
    texto = str(exc)
    if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
        return "cuota"
    if "503" in texto or "UNAVAILABLE" in texto or "500" in texto or "INTERNAL" in texto:
        return "transitorio"
    return None


def llamar_con_reintentos(fn, *args, reintentos=6, espera_base_seg=20.0, **kwargs):
    """Llama a fn(*args, **kwargs) reintentando en 429 (cuota) y en los errores
    transitorios del servidor (503/500). Cualquier otro error de API se propaga
    de inmediato: no tiene sentido reintentar un 400 o un 403.

    Seis intentos porque cuatro no alcanzaron: en la corrida 33994751090 el
    modelo estuvo sobrecargado más de los 140s que cubrían 3 esperas (20+40+80)
    y se cayó la etapa de plan, y con ella las de guion y video. Con seis, el
    backoff exponencial espera hasta ~10 minutos antes de rendirse, que es
    tiempo de runner desatendido y no de nadie mirando la pantalla."""
    for intento in range(1, reintentos + 1):
        # Se anota ANTES de llamar, y una vez por intento. Un 429 o un 503 no
        # cobran, así que contarlos sobra por unos pocos; a cambio, un bucle de
        # reintentos desbocado choca contra el tope en vez de seguir pidiendo.
        # Ese es justo el caso que hay que frenar (ver presupuesto.py).
        presupuesto.consumir("texto", 1, kwargs.get("model") or "texto")
        try:
            return fn(*args, **kwargs)
        except genai_errors.APIError as exc:
            motivo = _clasificar_error(exc)
            if motivo is None or intento == reintentos:
                raise
            if motivo == "cuota":
                espera = _extraer_retry_delay(exc, espera_base_seg * intento)
                detalle = "Cuota excedida"
            else:
                # El servidor no sugiere retryDelay en un 503: backoff
                # exponencial, que es lo que recomienda la propia API para un
                # modelo sobrecargado.
                espera = espera_base_seg * (2 ** (intento - 1))
                detalle = "Modelo no disponible (error transitorio)"
            logger.warning(
                f"{detalle} (intento {intento}/{reintentos}), "
                f"reintentando en {espera:.0f}s..."
            )
            time.sleep(espera)
    return None  # inalcanzable: el último intento siempre retorna o lanza
