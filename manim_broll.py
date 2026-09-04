"""
Manim B-roll — alternativa a veo_broll.py: genera el video de apoyo de cada
escena con Manim (motion graphics por código) en vez de Gemini Veo (video
generativo).

Ventaja frente a Veo: gratis, determinista y reproducible (mismo prompt ->
mismo resultado), ideal para nichos de estilo "explicador visual" (geometría,
física, espacio) con líneas/grid en vez de video fotorrealista. Desventaja:
no genera imágenes reales, solo animación vectorial.

Flujo: le pedimos a Gemini el *código* de una escena de Manim (no el video)
a partir del prompt_visual de la escena, y la renderizamos localmente con el
CLI de `manim`. Se cachea igual que veo_broll, por hash del prompt.

Requiere: pip install manim (y sus dependencias nativas: cairo, pango,
pkg-config — ver README), ffmpeg en el PATH.
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
CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "manim_cache")
DURACION_OBJETIVO_SEG = 8
TIMEOUT_RENDER_SEG = 180

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

_PROMPT_SISTEMA = """Eres un generador de escenas de Manim (Community Edition) para videos
educativos estilo "explicador visual minimalista" (grid neón sobre fondo oscuro,
líneas, formas geométricas, texto tipográfico simple, transiciones suaves — el
estilo de canales de divulgación matemática/física en TikTok/Shorts).

Reglas estrictas:
- Responde ÚNICAMENTE con código Python válido. Sin explicaciones, sin
  comentarios de markdown (nada de ```), sin texto antes o después.
- Debe definir exactamente una clase llamada `Escena` que herede de `Scene`.
- `from manim import *` al inicio.
- Fondo oscuro: `self.camera.background_color = "#0B0F14"` como primera línea
  de construct().
- Usa colores neón para los elementos principales (verdes/rosas/celestes
  saturados tipo "#00E28A", "#FF2D78", "#3DA9FC"), líneas finas, tipografía
  con `Text(...)` (nunca `MathTex` ni `Tex`: no hay LaTeX instalado).
- Duración total de la animación: entre 6 y 10 segundos de `run_time` sumado.
- Nada de assets externos (imágenes, SVGs, fuentes) ni de red.
- El video no lleva narración: es puramente visual, debe poder loopearse o
  recortarse sin que se vea roto (evita que termine en un frame vacío).
"""


logger = logging.getLogger("manim_broll")

_client = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _ruta_cache(prompt_visual, aspecto):
    clave = hashlib.sha256(f"{aspecto}|{prompt_visual}".encode("utf-8")).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"manim_{clave}.mp4")


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _limpiar_codigo(texto):
    texto = texto.strip()
    texto = re.sub(r'^```(?:python)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()


def _generar_codigo_escena(cliente, prompt_visual, modelo):
    respuesta = gemini_utils.llamar_con_reintentos(
        cliente.models.generate_content,
        model=modelo,
        contents=(
            f"{_PROMPT_SISTEMA}\n\n"
            f"Tema/idea visual de la escena (no la copies literal, "
            f"interprétala visualmente): {prompt_visual}"
        ),
    )
    codigo = _limpiar_codigo(respuesta.text or "")
    if "class Escena" not in codigo:
        raise ValueError("La respuesta de Gemini no define la clase 'Escena'.")
    return codigo


def _renderizar_codigo(codigo, aspecto, ruta_salida):
    w, h = RESOLUCIONES.get(aspecto, RESOLUCIONES["16:9"])
    with tempfile.TemporaryDirectory(prefix="manim_broll_") as tmp:
        script_path = os.path.join(tmp, "escena.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(codigo)

        media_dir = os.path.join(tmp, "media")
        cmd = [
            "manim", "-ql", "--fps", "30",
            "--resolution", f"{w},{h}",
            "--media_dir", media_dir,
            script_path, "Escena",
        ]
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_RENDER_SEG,
        )
        if res.returncode != 0:
            detalle = (res.stderr or res.stdout or "").strip()[-2000:]
            raise RuntimeError(f"manim falló (código {res.returncode}):\n{detalle}")

        candidatos = glob.glob(os.path.join(media_dir, "**", "Escena.mp4"), recursive=True)
        if not candidatos:
            raise RuntimeError("manim no generó Escena.mp4.")

        shutil.copyfile(candidatos[0], ruta_salida)


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT, reintentos=2):
    """Misma interfaz que veo_broll.generar_clip_cacheado: devuelve la ruta
    local a un clip de video para el prompt dado (generado con Manim en vez
    de Veo), o None si falló tras los reintentos."""
    ruta_salida = _ruta_cache(prompt_visual, aspecto)
    if _archivo_valido(ruta_salida):
        return ruta_salida

    cliente = _obtener_cliente()
    for intento in range(1, reintentos + 1):
        try:
            codigo = _generar_codigo_escena(cliente, prompt_visual, modelo)
            _renderizar_codigo(codigo, aspecto, ruta_salida)
            if _archivo_valido(ruta_salida):
                return ruta_salida
        except Exception as exc:
            logger.warning(f"Manim intento {intento}/{reintentos} falló: {exc}")

    return None
