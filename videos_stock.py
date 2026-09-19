"""
Videos de banco — b-roll de VIDEO real (no foto) de los bancos gratuitos
Pexels Videos y Pixabay Videos, con caída a fondos_stock (foto + Ken Burns)
cuando ninguno de los dos devuelve nada.

Por qué existe, si ya estaba fondos_stock: hasta ahora el formato emocional
resolvía cada plano con UNA foto fija y un zoom lento encima. Funciona, pero
se nota: el movimiento es siempre el mismo y no hay nada vivo en cuadro. Los
dos proyectos de referencia que trajo el usuario —gyoridavid/short-video-maker
y harry0703/MoneyPrinterTurbo— resuelven el mismo problema igual y sin gastar
un peso: los dos bancos tienen catálogo de VIDEO con la misma licencia libre y
la misma API key gratuita, y ninguno de los dos generan imagen por IA.

Tres cosas se copiaron de ahí porque resuelven fallas concretas que este
pipeline tenía:

1. Términos comodín (short-video-maker). Si la consulta específica del guion
   no devuelve nada, en vez de descartar el plano se reintenta con términos
   genéricos que siempre tienen catálogo ("naturaleza", "cielo", "océano"...).
   Antes un plano sin resultados quedaba directamente afuera del video.

2. No repetir clip dentro del mismo video (short-video-maker). Se lleva la
   lista de ids ya usados en la corrida y se descartan al elegir. Sin esto, dos
   escenas con consultas parecidas terminaban con el mismo clip idéntico.

3. Segunda fuente gratuita (MoneyPrinterTurbo). Pexels da 200 pedidos/hora;
   Pixabay es otra cuota independiente y otra key gratis. Con las dos, agotar
   o perder una no deja al pipeline sin b-roll.

Además se eligió entre TODOS los candidatos y no el primero: la búsqueda de
fotos pedía per_page=1, así que una consulta dada devolvía siempre la misma
imagen para siempre. Acá se pide un lote y se elige con un azar sembrado por
la consulta — sigue siendo determinista (la misma consulta da el mismo clip,
la caché sirve) pero dos consultas parecidas ya no chocan en el mismo archivo.

Requiere: PEXELS_API_KEY (gratis, sin tarjeta, https://www.pexels.com/api/).
Opcional: PIXABAY_API_KEY (gratis, sin tarjeta, https://pixabay.com/api/docs/).
Sin ninguna de las dos, el módulo cae a fondos_stock y el pipeline sigue.
"""
import os
import time
import random
import hashlib
import logging
import subprocess

import requests

import archivos
import fondos_stock

logger = logging.getLogger("videos_stock")

CARPETA_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pipeline_state", "videos_cache"
)
TIMEOUT_SEG = 30
# Descargar un mp4 es mucho más pesado que una foto: el timeout de la descarga
# va aparte del de la búsqueda, que sigue siendo un JSON chico.
TIMEOUT_DESCARGA_SEG = 120
PAUSA_ENTRE_PEDIDOS_SEG = 1.0

# Se piden varios y se elige entre ellos (ver docstring). 40 es el lote más
# grande que los dos bancos sirven sin paginar.
CANDIDATOS_POR_PEDIDO = 40

# Cuánto margen de sobra se le pide al clip por encima de la duración de la
# escena. Un video de banco declara su duración redondeada y a veces el último
# medio segundo es un fundido a negro; pedir de más evita quedarse corto.
MARGEN_DURACION_SEG = 2

# Cuando la consulta del guion no devuelve nada. Son deliberadamente genéricos
# y siempre tienen catálogo en los dos bancos: la idea es que un plano nunca
# quede sin imagen, no que el comodín sea bueno.
TERMINOS_COMODIN = ("naturaleza", "cielo", "océano", "ciudad noche", "bosque niebla")

# Ids de clips ya usados en esta corrida, para no repetir el mismo video en dos
# escenas del mismo día. Vive en memoria a propósito: el objetivo es la
# variedad DENTRO de un video, no prohibir para siempre un clip que quedó bien.
_ids_usados = set()

_ultimo_pedido = 0.0


def reiniciar_usados():
    """Limpia la lista de clips ya usados. El maestro la llama al empezar cada
    video: la restricción de no repetir es por video, no por corrida."""
    _ids_usados.clear()


def _limitar_ritmo():
    global _ultimo_pedido
    espera = PAUSA_ENTRE_PEDIDOS_SEG - (time.time() - _ultimo_pedido)
    if espera > 0:
        time.sleep(espera)
    _ultimo_pedido = time.time()


def _orientacion(aspecto):
    return {"9:16": "portrait", "16:9": "landscape"}.get(aspecto, "square")


def _elegir(candidatos, consulta):
    """Elige uno de los candidatos con azar sembrado por la consulta.

    Sembrado y no `random.choice` a secas para que la elección sea reproducible:
    la misma consulta tiene que resolver al mismo clip en cada corrida, si no la
    caché en disco no sirve de nada y cada corrida vuelve a descargar."""
    disponibles = [c for c in candidatos if c["id"] not in _ids_usados] or candidatos
    return random.Random(consulta).choice(disponibles)


def _buscar_pexels(consulta, aspecto):
    """Candidatos de Pexels Videos. Lista vacía si no hay key o no hay nada."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    _limitar_ritmo()
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params={"query": consulta, "orientation": _orientacion(aspecto),
                "size": "medium", "per_page": CANDIDATOS_POR_PEDIDO},
        timeout=TIMEOUT_SEG,
    )
    resp.raise_for_status()
    candidatos = []
    for video in resp.json().get("videos", []):
        # De todos los archivos del mismo video (varias calidades) se toma el
        # más grande que siga por debajo de 1920 de alto: arriba de eso son
        # 4K de decenas de MB que no aportan nada a un render de 1080p y sí
        # multiplican el tiempo de descarga.
        archivos_video = [
            f for f in video.get("video_files", [])
            if f.get("link") and (f.get("height") or 0) <= 1920
        ]
        if not archivos_video:
            continue
        mejor = max(archivos_video, key=lambda f: f.get("height") or 0)
        candidatos.append({
            "id": f"pexels-{video['id']}",
            "url": mejor["link"],
            "duracion": video.get("duration") or 0,
        })
    return candidatos


def _buscar_pixabay(consulta, aspecto):
    """Candidatos de Pixabay Videos. Segunda fuente, cuota independiente."""
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    _limitar_ritmo()
    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": consulta, "per_page": CANDIDATOS_POR_PEDIDO,
                "safesearch": "true"},
        timeout=TIMEOUT_SEG,
    )
    resp.raise_for_status()
    candidatos = []
    for video in resp.json().get("hits", []):
        # Pixabay no filtra por orientación en la API, así que el encuadre lo
        # arregla el recorte de _ajustar. "medium" es el tamaño que sirve para
        # 1080p sin bajar el master.
        archivo = (video.get("videos") or {}).get("medium") or {}
        if not archivo.get("url"):
            continue
        candidatos.append({
            "id": f"pixabay-{video['id']}",
            "url": archivo["url"],
            "duracion": video.get("duration") or 0,
        })
    return candidatos


def _candidatos(consulta, aspecto, duracion):
    """Junta los candidatos de las dos fuentes y descarta los más cortos que la
    escena. Un fallo de una fuente no tumba la otra: se registra y se sigue."""
    encontrados = []
    for buscar in (_buscar_pexels, _buscar_pixabay):
        try:
            encontrados += buscar(consulta, aspecto)
        except requests.RequestException as exc:
            logger.warning(f"{buscar.__name__} falló para '{consulta}': {exc}")
    minimo = duracion + MARGEN_DURACION_SEG
    # Si ninguno llega al mínimo se devuelven igual los que haya: _ajustar sabe
    # repetir en bucle un clip corto, y un clip corto en bucle es mejor que
    # quedarse sin plano.
    return [c for c in encontrados if c["duracion"] >= minimo] or encontrados


def _descargar(url, ruta_salida):
    resp = requests.get(url, timeout=TIMEOUT_DESCARGA_SEG, stream=True)
    resp.raise_for_status()
    with open(ruta_salida, "wb") as f:
        for trozo in resp.iter_content(chunk_size=1 << 16):
            f.write(trozo)
    return archivos.valido(ruta_salida)


def _ajustar(ruta_fuente, ancho, alto, duracion, ruta_salida, fps=30):
    """Lleva el clip descargado al encuadre y la duración exactos de la escena.

    -stream_loop -1 + -t: si el clip del banco es más corto que la escena se
    repite en bucle hasta llenarla, en vez de dejar negro al final. El recorte
    es centrado (scale a cubrir + crop) porque el banco entrega 16:9 aunque se
    pida vertical, y sin esto quedarían franjas negras arriba y abajo."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-stream_loop", "-1", "-i", ruta_fuente, "-t", str(duracion),
         "-vf", (f"scale={ancho}:{alto}:force_original_aspect_ratio=increase,"
                 f"crop={ancho}:{alto},fps={fps}"),
         "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         ruta_salida],
        check=True, timeout=180,
    )
    if not archivos.valido(ruta_salida):
        raise RuntimeError("ffmpeg no generó un clip válido desde el video de banco.")
    return ruta_salida


def generar_clip_cacheado(consulta, aspecto="9:16", duracion=6, reintentos=2):
    """Punto de entrada equivalente a fondos_stock/hyperframes_broll: devuelve
    la ruta a un clip de video para la consulta dada, o None.

    Cae a fondos_stock (foto + Ken Burns) si ninguna de las dos fuentes de video
    tiene algo. Esa caída es el motivo de que este módulo pueda ser el motor por
    defecto del formato emocional sin riesgo: en el peor caso da exactamente lo
    que daba antes."""
    consulta = fondos_stock.limpiar_consulta(consulta)
    clave = hashlib.sha256(f"{aspecto}|{duracion}|{consulta}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    ruta_clip = os.path.join(CARPETA_CACHE, f"clip_{clave}.mp4")
    if archivos.valido(ruta_clip):
        return ruta_clip

    for termino in (consulta,) + TERMINOS_COMODIN:
        candidatos = _candidatos(termino, aspecto, duracion)
        if not candidatos:
            logger.info(f"Sin videos de banco para '{termino}'.")
            continue
        elegido = _elegir(candidatos, consulta)

        from generar_video_maestro import RESOLUCIONES  # import tardío: evita el ciclo
        ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["9:16"])
        ruta_fuente = os.path.join(CARPETA_CACHE, f"fuente_{clave}.mp4")
        try:
            for intento in range(1, reintentos + 1):
                if _descargar(elegido["url"], ruta_fuente):
                    break
                if intento < reintentos:
                    time.sleep(3 * intento)
            else:
                continue
            _ajustar(ruta_fuente, ancho, alto, duracion, ruta_clip)
            _ids_usados.add(elegido["id"])
            return ruta_clip
        except (requests.RequestException, subprocess.SubprocessError, OSError, RuntimeError) as exc:
            logger.warning(f"No se pudo armar el clip de banco para '{termino}': {exc}")
        finally:
            # El master descargado no se guarda: pesa varias veces más que el
            # clip ya recortado y no se vuelve a usar (la caché es por clip).
            if os.path.exists(ruta_fuente):
                os.remove(ruta_fuente)

    logger.info(f"Sin video de banco para '{consulta}': se cae a foto fija.")
    return fondos_stock.generar_clip_cacheado(consulta, aspecto=aspecto,
                                              duracion=duracion, reintentos=reintentos)
