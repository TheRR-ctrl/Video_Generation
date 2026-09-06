"""
Fondos de banco — b-roll fotográfico real para el formato emocional/poético,
vía la API gratuita de Pexels. Alternativa a hyperframes_broll (diagramas) y a
veo_broll (video generado): acá el "dibujo" es una foto real con licencia
libre, con un leve Ken Burns para que no se sienta una diapositiva estática.

Por qué esto y no generación de imagen por IA: el formato emocional (una
escena evocadora + texto tipo carta que se revela encima) pide una ilustración
o fotografía con clima, no un diagrama. Generarla a medida por IA (Imagen,
Nano Banana) cuesta dinero por imagen y usa la misma API de Gemini que el
canal dejó de usar. Pexels es gratis, sin tarjeta, con una cuota generosa
(200 pedidos/hora, 20.000/mes — de sobra para un video por día) y sin límite
de uso comercial bajo su licencia.

Lo que se pierde frente a una imagen generada a medida: la foto es real, no
una ilustración pintada como la del canal de referencia, y no se arma
palabra por palabra para el guion — se busca por palabras clave. Para el
tono "carta/reflexión" (paisajes, siluetas, objetos cotidianos con luz
dramática) el banco de Pexels rinde bien igual.

Requiere: PEXELS_API_KEY (gratis en https://www.pexels.com/api/, sin tarjeta).
"""
import os
import re
import time
import hashlib
import logging
import subprocess

import requests

logger = logging.getLogger("fondos_stock")

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "fondos_cache")
API_BASE = "https://api.pexels.com/v1"
TIMEOUT_SEG = 20
# Free tier de Pexels: 200 pedidos/hora. Un margen entre pedidos evita
# agotarlo en una corrida con muchas escenas sin coordinación entre ellas.
PAUSA_ENTRE_PEDIDOS_SEG = 1.0

_ultimo_pedido = 0.0


class SinAPIKey(RuntimeError):
    pass


def _api_key():
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        raise SinAPIKey(
            "Falta PEXELS_API_KEY. Se consigue gratis, sin tarjeta, en "
            "https://www.pexels.com/api/ y se pone como secret del repo o "
            "variable de entorno."
        )
    return key


def _limitar_ritmo():
    global _ultimo_pedido
    espera = PAUSA_ENTRE_PEDIDOS_SEG - (time.time() - _ultimo_pedido)
    if espera > 0:
        time.sleep(espera)
    _ultimo_pedido = time.time()


def _ruta_cache(consulta, aspecto):
    clave = hashlib.sha256(f"{aspecto}|{consulta}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"foto_{clave}.jpg")


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


# Palabras de relleno que no aportan nada a una búsqueda de fotos y solo le
# roban lugar a las palabras concretas dentro del límite de 4.
_RELLENO = {
    "una", "unos", "unas", "los", "las", "del", "por", "con", "que", "para",
    "esta", "este", "sobre", "como", "sin", "más", "muy", "sus", "ese", "esa",
}


def _limpiar_consulta(texto):
    """Pexels busca mejor con 2-4 palabras concretas que con una frase larga:
    una consulta tipo oración le devuelve resultados genéricos o vacíos."""
    texto = re.sub(r"[^\w\sáéíóúñ]", " ", texto, flags=re.I)
    palabras = [p for p in texto.split() if len(p) > 2 and p.lower() not in _RELLENO][:4]
    return " ".join(palabras) or "atardecer melancolico"


def buscar_foto_cacheada(consulta, aspecto="9:16", reintentos=2):
    """Devuelve la ruta local a una foto para la consulta dada, bajándola de
    Pexels si no está ya en caché. None si falló."""
    consulta = _limpiar_consulta(consulta)
    ruta_salida = _ruta_cache(consulta, aspecto)
    if _archivo_valido(ruta_salida):
        return ruta_salida

    orientacion = "portrait" if aspecto == "9:16" else "landscape" if aspecto == "16:9" else "square"
    headers = {"Authorization": _api_key()}
    params = {"query": consulta, "orientation": orientacion, "per_page": 1, "size": "large"}

    for intento in range(1, reintentos + 1):
        try:
            _limitar_ritmo()
            resp = requests.get(f"{API_BASE}/search", headers=headers, params=params, timeout=TIMEOUT_SEG)
            resp.raise_for_status()
            fotos = resp.json().get("photos", [])
            if not fotos:
                logger.warning(f"Pexels no devolvió fotos para '{consulta}'.")
                return None
            # 'large2x' da buena resolución para 1080p sin bajar el original
            # entero (varios MB) que no hace falta para un short.
            url_imagen = fotos[0]["src"].get("large2x") or fotos[0]["src"]["original"]
            img = requests.get(url_imagen, timeout=TIMEOUT_SEG)
            img.raise_for_status()
            with open(ruta_salida, "wb") as f:
                f.write(img.content)
            if _archivo_valido(ruta_salida):
                return ruta_salida
        except requests.RequestException as exc:
            logger.warning(f"Pexels intento {intento}/{reintentos} falló para '{consulta}': {exc}")
            if intento < reintentos:
                time.sleep(3 * intento)

    return None


def _filtro_ken_burns(ancho, alto, duracion, fps, zoom_final=1.12):
    """Zoom lento y parejo sobre la foto entera, para que no se sienta una
    diapositiva estática. Nada de paneo: con una sola foto por escena, mover
    el encuadre corre el riesgo de salirse del área con contenido."""
    n_frames = max(1, round(duracion * fps))
    return (
        f"scale=-2:{alto*2}:force_original_aspect_ratio=increase,"
        f"crop={ancho*2}:{alto*2},"
        f"zoompan=z='min(zoom+{(zoom_final - 1) / n_frames:.6f},{zoom_final})':"
        f"d={n_frames}:s={ancho}x{alto}:fps={fps}"
    )


def clip_desde_foto(ruta_foto, ancho, alto, duracion, ruta_salida, fps=30):
    """Convierte una foto fija en un clip de video con Ken Burns sutil."""
    filtro = _filtro_ken_burns(ancho, alto, duracion, fps)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-loop", "1", "-i", ruta_foto, "-t", str(duracion),
         "-vf", filtro, "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         ruta_salida],
        check=True, timeout=60,
    )
    if not _archivo_valido(ruta_salida):
        raise RuntimeError("ffmpeg no generó un clip válido desde la foto.")
    return ruta_salida


def generar_clip_cacheado(consulta, aspecto="9:16", duracion=6, reintentos=2):
    """Punto de entrada equivalente a hyperframes_broll/veo_broll/manim_broll:
    devuelve la ruta a un clip de video para la consulta dada, o None."""
    clave = hashlib.sha256(f"{aspecto}|{duracion}|{_limpiar_consulta(consulta)}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    ruta_clip = os.path.join(CARPETA_CACHE, f"clip_{clave}.mp4")
    if _archivo_valido(ruta_clip):
        return ruta_clip

    foto = buscar_foto_cacheada(consulta, aspecto, reintentos)
    if not foto:
        return None

    from generar_video_maestro import RESOLUCIONES  # import tardío: evita el ciclo
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["9:16"])
    try:
        clip_desde_foto(foto, ancho, alto, duracion, ruta_clip)
        return ruta_clip
    except Exception as exc:
        logger.warning(f"No se pudo armar el clip desde la foto de '{consulta}': {exc}")
        return None
