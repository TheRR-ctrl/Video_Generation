"""
Reference Channels — dado un video o canal de YouTube, resuelve su channel_id
y trae los títulos de sus videos más recientes vía el feed RSS público del
canal (solo lectura, sin API key, mismo enfoque que trend_scout.py del
pipeline hermano video-scout-pipeline).

Se usa como "inspiración de estilo" para content_planner.py: los títulos NO
se copian ni parafrasean, solo se usan como ejemplo del tono/ángulo del
nicho para que el plan de contenido generado sea original.
"""
import re
import logging
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    requests = None

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
_RE_CHANNEL_ID_EN_URL = re.compile(r"/channel/(UC[\w-]{10,})")
_RE_EXTERNAL_ID = re.compile(r'"externalId":"(UC[\w-]{10,})"')
_RE_CHANNEL_ID_JSON = re.compile(r'"channelId":"(UC[\w-]{10,})"')

logger = logging.getLogger("reference_channels")


def _pedir(url, params=None, timeout=15):
    return requests.get(url, params=params, headers={"User-Agent": _UA_NAVEGADOR}, timeout=timeout)


def resolver_channel_id(url_referencia):
    """Acepta una URL de video, de canal (/channel/UC..., /@handle) y devuelve
    el channel_id (UC...), o None si no se pudo resolver."""
    if requests is None:
        raise RuntimeError("Falta el paquete 'requests'. Instálalo con: pip install requests")

    m = _RE_CHANNEL_ID_EN_URL.search(url_referencia)
    if m:
        return m.group(1)

    url_pagina = url_referencia
    if "/watch" in url_referencia or "youtu.be/" in url_referencia:
        # Es un video: primero resolvemos la página del canal vía oEmbed
        # (no requiere API key, es el mismo endpoint público que usa
        # cualquier embed de YouTube).
        try:
            resp = _pedir(
                "https://www.youtube.com/oembed",
                params={"url": url_referencia, "format": "json"},
            )
            resp.raise_for_status()
            url_pagina = resp.json().get("author_url")
            if not url_pagina:
                return None
        except Exception as exc:
            logger.warning(f"No se pudo resolver el canal desde el video {url_referencia}: {exc}")
            return None

    try:
        resp = _pedir(url_pagina)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(f"No se pudo leer la página del canal {url_pagina}: {exc}")
        return None

    m = _RE_EXTERNAL_ID.search(resp.text) or _RE_CHANNEL_ID_JSON.search(resp.text)
    return m.group(1) if m else None


def obtener_videos_recientes(channel_id, limite=15):
    """Lee el feed RSS/Atom público del canal (solo lectura, sin login) y
    devuelve una lista de dicts {titulo, descripcion, video_id, publicado}."""
    if requests is None:
        raise RuntimeError("Falta el paquete 'requests'. Instálalo con: pip install requests")

    resp = _pedir(
        "https://www.youtube.com/feeds/videos.xml",
        params={"channel_id": channel_id},
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    nombre_canal = root.findtext("a:title", default="", namespaces=_NS) or channel_id
    videos = []
    for entry in root.findall("a:entry", _NS)[:limite]:
        titulo = entry.findtext("a:title", default="", namespaces=_NS) or ""
        video_id = entry.findtext("yt:videoId", default="", namespaces=_NS) or ""
        publicado = entry.findtext("a:published", default="", namespaces=_NS) or ""
        descripcion = ""
        grupo = entry.find("{http://search.yahoo.com/mrss/}group")
        if grupo is not None:
            descripcion = grupo.findtext(
                "{http://search.yahoo.com/mrss/}description", default=""
            ) or ""
        videos.append({
            "titulo": titulo,
            "descripcion": descripcion[:280],
            "video_id": video_id,
            "publicado": publicado,
        })
    return nombre_canal, videos


def recolectar_referencias(urls_referencia, videos_por_canal=15):
    """Resuelve una lista de URLs (video o canal) a sus videos recientes.
    Los canales que no se puedan resolver se omiten con un warning (no
    interrumpen el resto)."""
    referencias = []
    vistos = set()

    for url in urls_referencia:
        channel_id = resolver_channel_id(url)
        if not channel_id or channel_id in vistos:
            if not channel_id:
                logger.warning(f"No se pudo resolver el canal para: {url}")
            continue
        vistos.add(channel_id)

        try:
            nombre_canal, videos = obtener_videos_recientes(channel_id, videos_por_canal)
        except Exception as exc:
            logger.warning(f"No se pudo leer el feed del canal {channel_id}: {exc}")
            continue

        if videos:
            referencias.append({
                "channel_id": channel_id,
                "nombre_canal": nombre_canal,
                "videos": videos,
            })

    return referencias
