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
import glob
import shutil
import hashlib
import logging
import tempfile
import subprocess

import hyperframes_broll
import plantillas_curiosidades

logger = logging.getLogger("curiosidades_broll")

CARPETA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state", "curiosidades_cache")

# Igual que estoico_broll.VERSION_MEZCLA: si cambia cómo se dibuja el gráfico
# (plantillas_curiosidades.py), esta versión invalida lo cacheado por
# corridas anteriores en vez de reusar un render viejo para siempre.
VERSION_PLANTILLA = "curiosidades-v1"


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _ruta_cache(plano_texto, ancho, alto, duracion):
    clave = hashlib.sha256(
        f"{VERSION_PLANTILLA}|{ancho}x{alto}|{duracion}|{plano_texto}".encode("utf-8")
    ).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"curiosidad_{clave}.mp4")


def _renderizar(plano_texto, ancho, alto, duracion, ruta_salida):
    """Render dedicado, igual criterio que estoico_broll._renderizar_sello: es
    HTML propio y determinista (no la respuesta de un modelo que podría venir
    vacía), así que un código de salida 0 del CLI de HyperFrames ya confirma
    que el clip es válido — no hace falta el chequeo de cobertura de píxeles
    de hyperframes_broll._renderizar_composicion, pensado para diagramas
    rellenos, no para líneas finas de rayo/onda sobre fondo casi negro."""
    if not hyperframes_broll._archivo_valido(hyperframes_broll.RUTA_GSAP_VENDOR):
        raise RuntimeError(f"No se encontró {hyperframes_broll.RUTA_GSAP_VENDOR} (gsap.min.js vendorizado).")

    html = plantillas_curiosidades.construir_html(plano_texto, ancho, alto, duracion)
    with tempfile.TemporaryDirectory(prefix="curiosidades_") as tmp:
        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        shutil.copyfile(hyperframes_broll.RUTA_GSAP_VENDOR, os.path.join(tmp, "gsap.min.js"))
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
            f.write('{"id": "curiosidad", "name": "Curiosidad"}')

        errores = hyperframes_broll._lint(tmp)
        if errores:
            raise RuntimeError(errores)

        res = subprocess.run(
            ["npx", "--yes", f"hyperframes@{hyperframes_broll.VERSION_CLI}", "render"],
            cwd=tmp, capture_output=True, text=True, timeout=hyperframes_broll.TIMEOUT_RENDER_SEG,
            env=hyperframes_broll._entorno_cli(),
        )
        if res.returncode != 0:
            detalle = (res.stderr or res.stdout or "").strip()[-2000:]
            raise RuntimeError(f"hyperframes render falló (código {res.returncode}):\n{detalle}")

        candidatos = glob.glob(os.path.join(tmp, "renders", "*.mp4"))
        if not candidatos:
            raise RuntimeError("hyperframes render no generó ningún mp4 en renders/.")
        ruta_render = max(candidatos, key=os.path.getmtime)
        shutil.copyfile(ruta_render, ruta_salida)
    if not _archivo_valido(ruta_salida):
        raise RuntimeError("El render de curiosidades no produjo un archivo válido.")


def generar_clip_cacheado(plano_texto, aspecto="9:16", duracion=6, reintentos=2):
    """Punto de entrada equivalente a estoico_broll/fondos_stock: devuelve la
    ruta a un clip de video para el plano dado, o None si falló."""
    from generar_video_maestro import RESOLUCIONES  # import tardío: evita el ciclo, igual que estoico_broll
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["9:16"])
    ruta_salida = _ruta_cache(plano_texto, ancho, alto, duracion)
    if _archivo_valido(ruta_salida):
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
