"""
Curiosidades b-roll — renderiza cada plano del formato "curiosidades" con
plantillas_curiosidades.py (gráficos animados por código: rayo, barra, onda,
ruido) sobre HyperFrames. Sin API de imagen y sin fotos de banco: a diferencia
de "estoico" (glifo + foto real) o "fotos" (solo foto), acá todo el cuadro
—incluida la lluvia y el reflejo de fondo— es dibujado por código, así que no
hace falta mezclar dos clips ni depender de PEXELS_API_KEY.

Nace del pedido de reproducir la estética de un video de referencia de
divulgación científica (rayos, campos eléctricos, ondas) sin pagar por un
motor de imagen/video generativo: cada arquetipo es una función Python pura
que dibuja SVG/HTML determinista (ver plantillas_curiosidades.py para el
porqué de cada decisión técnica: nada de motores de partículas ni RNG externo,
todo semilla-Python-a-HTML-estático, igual criterio que plantillas_sello.py).

Es su propio motor de render (no pasa por hyperframes_broll.generar_clips_lote_
cacheados, pensado para el batching por Gemini de plantillas_broll): un plano
de curiosidades no necesita ningún modelo para producir su HTML, así que un
render directo por plano —cacheado en disco, igual que estoico_broll.py— es
más simple y no gasta cuota.
"""
import os
import hashlib
import logging

import archivos
import hyperframes_broll
import plantillas_curiosidades

logger = logging.getLogger("curiosidades_broll")

CARPETA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state", "curiosidades_cache")

# Igual que estoico_broll.VERSION_MEZCLA: si cambia cómo se dibuja el gráfico
# (plantillas_curiosidades.py), esta versión invalida lo cacheado por
# corridas anteriores en vez de reusar un render viejo para siempre.
VERSION_PLANTILLA = "curiosidades-v1"


def _ruta_cache(plano_texto, ancho, alto, duracion):
    clave = hashlib.sha256(
        f"{VERSION_PLANTILLA}|{ancho}x{alto}|{duracion}|{plano_texto}".encode("utf-8")
    ).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"curiosidad_{clave}.mp4")


def _renderizar(plano_texto, ancho, alto, duracion, ruta_salida):
    """Dibuja el gráfico del plano y lo renderiza a mp4.

    verificar_contenido=False por el mismo motivo que en estoico_broll: el
    chequeo de cobertura de píxeles está pensado para diagramas rellenos, y
    descartaría un rayo o una onda de línea fina sobre fondo casi negro aunque
    el render haya salido perfecto."""
    html = plantillas_curiosidades.construir_html(plano_texto, ancho, alto, duracion)
    hyperframes_broll.renderizar_html(
        html, ruta_salida, nombre="curiosidad", verificar_contenido=False
    )


def generar_clip_cacheado(plano_texto, aspecto="9:16", duracion=6, reintentos=2):
    """Punto de entrada equivalente a estoico_broll/fondos_stock: devuelve la
    ruta a un clip de video para el plano dado, o None si falló."""
    from generar_video_maestro import RESOLUCIONES  # import tardío: evita el ciclo, igual que estoico_broll
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["9:16"])
    ruta_salida = _ruta_cache(plano_texto, ancho, alto, duracion)
    if archivos.valido(ruta_salida):
        return ruta_salida

    ultimo_error = None
    for intento in range(reintentos + 1):
        try:
            _renderizar(plano_texto, ancho, alto, duracion, ruta_salida)
            return ruta_salida
        except Exception as exc:
            ultimo_error = exc
            logger.warning(f"Intento {intento + 1} falló para '{plano_texto}': {exc}")
    logger.warning(f"No se pudo generar el clip de curiosidades para '{plano_texto}': {ultimo_error}")
    return None
