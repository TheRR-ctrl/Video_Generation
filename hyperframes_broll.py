"""
HyperFrames B-roll — segunda alternativa a veo_broll.py (junto a
manim_broll.py). Genera el video de apoyo de cada escena pidiéndole a Gemini
el HTML de una composición de HyperFrames (https://github.com/heygen-com/hyperframes:
HTML + CSS + GSAP -> mp4 determinista vía Chrome headless) en vez de un video
fotorrealista con Veo.

Frente a Manim: HyperFrames encaja mejor para motion graphics tipo "anuncio"
(texto kinético, transiciones, formas simples animadas con easings
declarativos) que para geometría/matemática exacta, donde Manim es más
natural. Mismo trato que veo_broll/manim_broll: gratis, determinista,
cacheado por prompt en pipeline_state/hyperframes_cache/.

Requiere: Node.js 22+, ffmpeg en el PATH. No requiere instalar el paquete
`hyperframes` de antemano: se invoca con `npx hyperframes@<version>`, pinneado
para que el render sea reproducible en el tiempo.

Credenciales: GEMINI_API_KEY (mismo que el resto del pipeline).
"""
import os
import re
import glob
import shutil
import hashlib
import logging
import tempfile
import subprocess

from google import genai

import gemini_utils

MODELO_TEXTO_DEFAULT = "gemini-3.6-flash"
VERSION_CLI = "0.8.27"
CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "hyperframes_cache")
RUTA_GSAP_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "gsap.min.js")
DURACION_ESCENA_SEG = 8
TIMEOUT_RENDER_SEG = 180

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

_PROMPT_SISTEMA = """Eres un generador de composiciones de HyperFrames (HTML + CSS + GSAP
-> video, ver hyperframes.heygen.com) para motion graphics estilo "explicador
visual minimalista" (grid neón sobre fondo oscuro, formas simples, texto
tipográfico, transiciones con easings suaves — el estilo de canales de
divulgación en TikTok/Shorts).

Reglas estrictas del contrato de HyperFrames (romperlas invalida el render):
- Responde ÚNICAMENTE con el HTML completo del archivo, empezando en
  "<!doctype html>". Sin explicaciones, sin markdown, sin texto antes o después.
- `<script src="gsap.min.js"></script>` en el <head> (SIEMPRE esa ruta
  relativa exacta — NUNCA un CDN ni otra URL, el archivo se copia local).
- No cargues ninguna otra URL externa (fuentes, imágenes, CDNs): tiene que
  renderizar sin red.
- El elemento raíz debe tener `id="root"`, `data-composition-id="main"`,
  `data-start="0"`, `data-duration="{duracion}"`, `data-width="{ancho}"`,
  `data-height="{alto}"`.
- Cada elemento animado dentro necesita `class="clip"`, `data-start` y
  `data-duration` (en segundos, dentro del rango de la composición).
- El timeline de GSAP debe crearse pausado y registrarse así (obligatorio,
  al final de un <script> inline):
    window.__timelines = window.__timelines || {{}};
    window.__timelines["main"] = tl;  // tl = gsap.timeline({{ paused: true }})
- Fondo oscuro (#0b0f14), colores neón para los elementos principales
  (verdes/rosas/celestes saturados: #00e28a, #ff2d78, #3da9fc), formas con
  SVG o divs, tipografía del sistema (no importes fuentes web).
- Nada de `Date.now()` ni `Math.random()` (el render debe ser 100%
  determinista, mismo resultado en cada frame sin importar el orden en que
  se pidan).
- No hay narración: el video es puramente visual, de {duracion} segundos.
"""


logger = logging.getLogger("hyperframes_broll")

_client = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _ruta_cache(prompt_visual, aspecto):
    clave = hashlib.sha256(f"{aspecto}|{prompt_visual}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"hf_{clave}.mp4")


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _limpiar_html(texto):
    texto = texto.strip()
    texto = re.sub(r'^```(?:html)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()


def _generar_composicion(cliente, prompt_visual, aspecto, modelo):
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["16:9"])
    instrucciones = _PROMPT_SISTEMA.format(duracion=DURACION_ESCENA_SEG, ancho=ancho, alto=alto)
    respuesta = gemini_utils.llamar_con_reintentos(
        cliente.models.generate_content,
        model=modelo,
        contents=(
            f"{instrucciones}\n\n"
            f"Tema/idea visual de la escena (no la copies literal, "
            f"interprétala visualmente): {prompt_visual}"
        ),
    )
    html = _limpiar_html(respuesta.text or "")
    if "id=\"root\"" not in html or "__timelines" not in html:
        raise ValueError("La respuesta de Gemini no cumple el contrato de HyperFrames.")
    return html


def _renderizar_composicion(html, ruta_salida):
    if not _archivo_valido(RUTA_GSAP_VENDOR):
        raise RuntimeError(f"No se encontró {RUTA_GSAP_VENDOR} (gsap.min.js vendorizado).")

    with tempfile.TemporaryDirectory(prefix="hyperframes_broll_") as tmp:
        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        shutil.copyfile(RUTA_GSAP_VENDOR, os.path.join(tmp, "gsap.min.js"))
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
            f.write('{"id": "escena", "name": "Escena"}')

        env = dict(os.environ)
        env["HYPERFRAMES_SKIP_SKILLS"] = "1"
        env["HYPERFRAMES_TELEMETRY_DISABLED"] = "1"

        res = subprocess.run(
            ["npx", "--yes", f"hyperframes@{VERSION_CLI}", "render"],
            cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_RENDER_SEG, env=env,
        )
        if res.returncode != 0:
            detalle = (res.stderr or res.stdout or "").strip()[-2000:]
            raise RuntimeError(f"hyperframes render falló (código {res.returncode}):\n{detalle}")

        candidatos = glob.glob(os.path.join(tmp, "renders", "*.mp4"))
        if not candidatos:
            raise RuntimeError("hyperframes render no generó ningún mp4 en renders/.")

        shutil.copyfile(max(candidatos, key=os.path.getmtime), ruta_salida)


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT, reintentos=2):
    """Misma interfaz que veo_broll/manim_broll.generar_clip_cacheado: devuelve
    la ruta local a un clip de video para el prompt dado (generado con
    HyperFrames), o None si falló tras los reintentos."""
    ruta_salida = _ruta_cache(prompt_visual, aspecto)
    if _archivo_valido(ruta_salida):
        return ruta_salida

    cliente = _obtener_cliente()
    for intento in range(1, reintentos + 1):
        try:
            html = _generar_composicion(cliente, prompt_visual, aspecto, modelo)
            _renderizar_composicion(html, ruta_salida)
            if _archivo_valido(ruta_salida):
                return ruta_salida
        except Exception as exc:
            logger.warning(f"HyperFrames intento {intento}/{reintentos} falló: {exc}")

    return None
