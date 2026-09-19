"""
Scout de Frases — busca frases de alto impacto (reflexiones cortas,
citas, realizaciones) para alimentar guiones y ganchos de video.

Salida: pipeline_state/frases_candidatas.json
Cada corrida evita repetir frases ya vistas (pipeline_state/frases_vistas.json).

Es una herramienta de INVESTIGACIÓN, no de escritura: junta material crudo en
inglés para que el guion (que acá se escribe a mano, ver guion.semilla.txt)
tenga de dónde sacar ángulos e ideas — no genera el guion ni traduce
automáticamente. Traducir/adaptar bien una frase de impacto es trabajo de
guionista, no de scraper, y automatizarlo con un modelo reintroduciría la
API que el canal dejó de usar.

Dos fuentes, las dos gratis y sin necesitar cuenta ni key:

1. **RSS de Reddit** (mismo mecanismo ya probado en video-scout-pipeline/
   trend_scout.py: el JSON público está bloqueado por el filtro anti-bot,
   pero el feed RSS/Atom de cada subreddit no). Acá el título del post ES la
   frase —a diferencia de trend_scout, que leía el cuerpo de historias— así
   que se toma el título, se limpia de tags tipo "[Image]" y se separa el
   autor cuando el post trae "Frase — Autor".
   Subreddits elegidos por densidad de frases cortas de impacto:
   r/Showerthoughts (realizaciones), r/GetMotivated y r/quotes (citas),
   r/DeepThoughts (reflexiones breves).

2. **ZenQuotes.io** (API pública, sin key, gratis mientras se respete el
   límite de 5 pedidos/30s y se dé atribución — ver ATRIBUCION_ZENQUOTES).
   Trae citas clásicas curadas, con autor siempre presente.

Requiere: pip install requests
"""
import os
import re
import html
import json
import time
import logging
from xml.etree import ElementTree as ET

import requests

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
RUTA_CANDIDATOS = os.path.join(CARPETA_ESTADO, "frases_candidatas.json")
RUTA_VISTAS = os.path.join(CARPETA_ESTADO, "frases_vistas.json")
RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_frases.json")

RATE_LIMIT_REDDIT_SEG = 12.0
RATE_LIMIT_ZENQUOTES_SEG = 6.5  # límite real: 5 pedidos/30s: 6s de margen entre pedidos

ATRIBUCION_ZENQUOTES = "Inspirational Quotes provided by ZenQuotes API (zenquotes.io)"

CONFIG_DEFAULT = {
    "subreddits": ["Showerthoughts", "GetMotivated", "quotes", "DeepThoughts"],
    "time_filter": "day",
    "limite_por_subreddit": 25,
    "min_palabras_frase": 4,
    "max_palabras_frase": 28,
    "incluir_zenquotes": True,
    "max_candidatos_salida": 40,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scout_frases")

_UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NS = {"a": "http://www.w3.org/2005/Atom"}

# Tags que Reddit/los propios usuarios agregan al principio o al final del
# título y que no son parte de la frase: "[Image]", "(OC)", "[x-post]", etc.
_RE_TAG_FINAL = re.compile(r"\s*[\[\(][^\]\)]{1,40}[\]\)]\s*$")
_RE_TAG_INICIO = re.compile(r"^\s*[\[\(][^\]\)]{1,40}[\]\)]\s*")
# Muchos posts de GetMotivated/quotes traen "Frase — Autor" o "Frase - Autor".
_RE_AUTOR_FINAL = re.compile(r"\s+[-—–]\s*([A-ZÁÉÍÓÚÑ][\w.' ]{2,40})$")


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def cargar_vistas():
    if os.path.exists(RUTA_VISTAS):
        with open(RUTA_VISTAS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistas(vistas):
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_VISTAS, "w", encoding="utf-8") as f:
        json.dump(sorted(vistas), f, ensure_ascii=False, indent=2)


def _limpiar_titulo(titulo):
    titulo = html.unescape(titulo or "").strip()
    anterior = None
    while anterior != titulo:
        anterior = titulo
        titulo = _RE_TAG_INICIO.sub("", titulo)
        titulo = _RE_TAG_FINAL.sub("", titulo).strip()
    return titulo.strip('"“”\' ')


def _separar_autor(frase):
    m = _RE_AUTOR_FINAL.search(frase)
    if not m:
        return frase, None
    return frase[:m.start()].strip('"“”\' '), m.group(1).strip()


def _obtener_posts_reddit(subreddit, cfg):
    """Lee el feed RSS/Atom público 'top' de un subreddit (solo lectura)."""
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss"
    params = {"t": cfg["time_filter"], "limit": cfg["limite_por_subreddit"]}
    headers = {"User-Agent": _UA_NAVEGADOR}

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code == 429:
        time.sleep(RATE_LIMIT_REDDIT_SEG * 2)
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code in (429, 403):
        raise RuntimeError(f"Bloqueado por reddit.com ({resp.status_code}).")
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    posts = []
    for entry in root.findall("a:entry", _NS):
        post_id = (entry.findtext("a:id", default="", namespaces=_NS) or "").replace("t3_", "")
        titulo = entry.findtext("a:title", default="", namespaces=_NS) or ""
        link_el = entry.find("a:link", _NS)
        url_post = link_el.get("href") if link_el is not None else ""
        posts.append({"id": post_id, "titulo": titulo, "url": url_post})
    return posts


def _escanear_reddit(cfg, vistas):
    candidatos = []
    for i, nombre_sub in enumerate(cfg["subreddits"]):
        if i > 0:
            time.sleep(RATE_LIMIT_REDDIT_SEG)
        logger.info(f"Escaneando r/{nombre_sub}...")
        try:
            posts = _obtener_posts_reddit(nombre_sub, cfg)
        except Exception as exc:
            logger.warning(f"No se pudo leer r/{nombre_sub}: {exc}")
            continue

        for rank, post in enumerate(posts):
            clave = f"reddit:{post['id']}"
            if not post["id"] or clave in vistas:
                continue
            frase = _limpiar_titulo(post["titulo"])
            if not frase:
                continue
            frase, autor = _separar_autor(frase)
            n_palabras = len(frase.split())
            if not (cfg["min_palabras_frase"] <= n_palabras <= cfg["max_palabras_frase"]):
                continue
            candidatos.append({
                "clave": clave,
                "frase": frase,
                "idioma": "en",
                "autor": autor,
                "fuente": f"r/{nombre_sub}",
                "url": post["url"],
                "rank_en_fuente": rank,
            })
            vistas.add(clave)
    return candidatos


def _escanear_zenquotes(vistas):
    """Citas clásicas curadas, con autor siempre presente. Requiere dar
    atribución (ver ATRIBUCION_ZENQUOTES) mientras se use sin API key."""
    try:
        time.sleep(RATE_LIMIT_ZENQUOTES_SEG)
        resp = requests.get("https://zenquotes.io/api/quotes", timeout=15)
        resp.raise_for_status()
        datos = resp.json()
    except Exception as exc:
        logger.warning(f"No se pudo leer ZenQuotes: {exc}")
        return []

    candidatos = []
    for item in datos:
        frase = html.unescape((item.get("q") or "").strip())
        autor = html.unescape((item.get("a") or "").strip()) or None
        if not frase:
            continue
        clave = "zenquotes:" + frase.lower()
        if clave in vistas:
            continue
        candidatos.append({
            "clave": clave,
            "frase": frase,
            "idioma": "en",
            "autor": autor,
            "fuente": "zenquotes.io",
            "url": None,
            "rank_en_fuente": len(candidatos),
        })
        vistas.add(clave)
    return candidatos


def escanear(cfg):
    vistas = cargar_vistas()
    candidatos = _escanear_reddit(cfg, vistas)
    if cfg.get("incluir_zenquotes", True):
        candidatos += _escanear_zenquotes(vistas)

    candidatos.sort(key=lambda c: (c["fuente"], c["rank_en_fuente"]))
    candidatos = candidatos[:cfg["max_candidatos_salida"]]

    guardar_vistas(vistas)
    return candidatos


def main():
    cfg = cargar_config()
    candidatos = escanear(cfg)

    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    salida = {"atribucion_zenquotes": ATRIBUCION_ZENQUOTES, "candidatos": candidatos}
    with open(RUTA_CANDIDATOS, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    logger.info(f"{len(candidatos)} frase(s) candidata(s) guardada(s) en {RUTA_CANDIDATOS}")
    for c in candidatos:
        firma = f" — {c['autor']}" if c["autor"] else ""
        print(f" • [{c['fuente']}] {c['frase']}{firma}")


if __name__ == "__main__":
    main()
