"""
Estoico b-roll — combina un glifo animado (plantillas_sello.py, dibujado por
código con HyperFrames) con una foto real de banco (fondos_stock.py/Pexels)
en una sola toma, para el formato "estoico" (reflexiones breves sobre dolor,
disciplina y templanza).

Nace de un pedido concreto: el usuario mostró un video de referencia con una
identidad visual fija y reconocible (un personaje pintado por IA, repetido
en todo el video, sobre fondos que cambian). Pintar ese personaje con IA
generativa (Gemini/Nano Banana) es plata por imagen y la misma API que el
canal dejó de usar para eso. La respuesta que no cuesta nada: en vez de un
personaje fijo, un GLIFO fijo por arquetipo (ver plantillas_sello.py),
dibujado por código, mezclado ópticamente sobre la foto — doble exposición,
no un recorte.

Cómo se superponen las dos capas sin croma: HyperFrames no exporta video con
transparencia, así que el glifo llega dibujado en blanco/dorado sobre negro
puro (#000000) y se le arma el canal alfa a partir de su propia luminancia
(`alphamerge` + `overlay`, ver _mezclar): donde es negro queda transparente y
la foto pasa intacta; donde tiene luz, se compone encima. La primera versión
usaba `blend=all_mode=screen`, que es la técnica estándar para esto, pero ese
modo salió roto en el ffmpeg del runner — el detalle está en _mezclar.

Requiere lo mismo que sus dos partes: PEXELS_API_KEY (fondos_stock) y
Node.js + ffmpeg (hyperframes_broll, ya requeridos por el pipeline).
"""
import os
import hashlib
import logging
import subprocess

import archivos
import fondos_stock
import plantillas_sello
import hyperframes_broll

logger = logging.getLogger("estoico_broll")

CARPETA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state", "estoico_cache")

_CARACTERES_INVALIDOS_ARTEFACTO = set('"<>:|*?\r\n')


def _purgar_nombres_invalidos():
    """Borra de la caché cualquier archivo con un carácter que actions/upload-
    artifact rechaza (rompió las corridas 34105169959 y 34105601948: un nombre
    viejo con ":" quedó persistido en actions/cache —esa etapa sí había tenido
    éxito, solo falló la subida del artefacto— y se seguía restaurando en cada
    corrida siguiente aunque el código ya no lo generara con ese nombre. Sin
    esto, el archivo colgado revienta la subida para siempre."""
    if not os.path.isdir(CARPETA_CACHE):
        return
    for nombre in os.listdir(CARPETA_CACHE):
        if _CARACTERES_INVALIDOS_ARTEFACTO & set(nombre):
            ruta = os.path.join(CARPETA_CACHE, nombre)
            try:
                os.remove(ruta)
                logger.warning(f"Purgado archivo de caché con carácter inválido: {nombre}")
            except OSError as exc:
                logger.warning(f"No se pudo purgar {nombre}: {exc}")


# Se suma a la clave de caché del COMPOSITE (no del glifo suelo): si cambia
# cómo se mezclan foto+glifo (como pasó de blend=screen, roto, a alphamerge+
# overlay), hay que invalidar los archivos ya cacheados de la corrida
# anterior — actions/cache los persiste entre corridas, así que sin esto el
# composite viejo (con el magenta) se seguiría reusando para siempre.
VERSION_MEZCLA = "alphamerge-v1"


def _ruta_cache(sello, consulta, aspecto, duracion):
    clave = hashlib.sha256(
        f"{VERSION_MEZCLA}|{aspecto}|{duracion}|{sello}|{consulta}".encode("utf-8")
    ).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"estoico_{clave}.mp4")


def _renderizar_sello(sello, ancho, alto, duracion, ruta_salida):
    """Dibuja el glifo y lo renderiza a mp4.

    Va con verificar_contenido=False: ese chequeo descarta el clip si el 78%
    superior del cuadro no llega a un 2% de píxeles encendidos, un umbral
    pensado para diagramas rellenos (barras, cajas). Un glifo de línea fina
    (ver plantillas_sello.py) es deliberadamente minimalista y no llega a esa
    cobertura aunque el render sea perfecto — se verificó a ojo. Al ser HTML
    propio y determinista (no la respuesta de un modelo que podría venir
    vacía), ese chequeo anti-fallo no hace falta acá."""
    html = plantillas_sello.construir_html(sello, ancho, alto, duracion)
    hyperframes_broll.renderizar_html(
        html, ruta_salida, nombre="sello", verificar_contenido=False
    )


def _mezclar(ruta_foto_clip, ruta_sello_clip, duracion, ruta_salida):
    """Superpone el glifo sobre la foto usando su propia luminancia como canal
    alfa (negro = transparente, blanco/dorado = opaco), no `blend=all_mode=
    screen`.

    Se probó primero con blend=screen (la técnica estándar de "doble
    exposición" en edición de video) pero en este ffmpeg (6.1.1-3ubuntu5) esa
    familia de modos aritméticos (screen/addition/lighten) da resultados
    imposibles incluso mezclando negro puro sólido con un color sólido
    conocido: screen(marrón, negro) devolvía magenta en vez del marrón
    original — verificado numéricamente pixel a pixel, con y sin forzar RGB
    antes del blend. "normal" (passthrough) funcionaba perfecto, aislando el
    bug a esos modos aritméticos específicos de esta build.

    alphamerge + overlay evita el problema de raíz: en vez de sumar valores
    de color (donde un "negro casi negro" por compresión de video puede
    filtrarse), extrae la luminancia del glifo como canal alfa real y
    compone con overlay, que si maneja alfa correctamente. Verificado
    pixel a pixel: el fondo queda exactamente igual donde el glifo es negro."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", ruta_foto_clip, "-i", ruta_sello_clip,
         "-filter_complex",
         "[1:v]split[c][a];[a]format=gray[alpha];[c][alpha]alphamerge[glifo];"
         "[0:v][glifo]overlay=format=auto",
         "-t", str(duracion), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         ruta_salida],
        check=True, timeout=60,
    )
    if not archivos.valido(ruta_salida):
        raise RuntimeError("ffmpeg no generó un clip válido al mezclar foto + sello.")
    return ruta_salida


def generar_clip_cacheado(plano_texto, aspecto="9:16", duracion=6, reintentos=2):
    """Punto de entrada equivalente a fondos_stock/hyperframes_broll: devuelve
    la ruta a un clip de video para el plano dado (foto + glifo mezclados), o
    None si falló. `plano_texto` trae el arquetipo entre corchetes y la
    consulta de foto, ej.: "[grieta] muro agrietado luz dorada"."""
    _purgar_nombres_invalidos()
    sello, consulta = plantillas_sello.extraer_sello_y_consulta(plano_texto)
    ruta_salida = _ruta_cache(sello, consulta, aspecto, duracion)
    if archivos.valido(ruta_salida):
        return ruta_salida

    foto_clip = fondos_stock.generar_clip_cacheado(consulta, aspecto=aspecto, duracion=duracion, reintentos=reintentos)
    if not foto_clip:
        logger.warning(f"Sin foto para '{consulta}' (sello={sello}): se omite el plano.")
        return None

    from generar_video_maestro import RESOLUCIONES  # import tardío: evita el ciclo, igual que fondos_stock
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["9:16"])
    # Nombre de archivo por ancho x alto, no por "aspecto" tal cual: "9:16" trae
    # ":", que actions/upload-artifact rechaza al subir el artefacto (se vio en
    # la corrida 34105169959 — el video se generó bien, pero la subida del
    # artefacto entero abortó por este único archivo).
    ruta_sello = os.path.join(CARPETA_CACHE, f"sello_{sello}_{ancho}x{alto}_{duracion}.mp4")
    try:
        if not archivos.valido(ruta_sello):
            os.makedirs(CARPETA_CACHE, exist_ok=True)
            _renderizar_sello(sello, ancho, alto, duracion, ruta_sello)
        _mezclar(foto_clip, ruta_sello, duracion, ruta_salida)
        return ruta_salida
    except Exception as exc:
        logger.warning(f"No se pudo armar el clip estoico para '{consulta}' (sello={sello}): {exc}")
        return None
