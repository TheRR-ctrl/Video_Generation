"""
HyperFrames B-roll — tercer motor de video de apoyo: genera el clip de cada
escena como una *composición HTML* (HTML + CSS + GSAP) y la renderiza a MP4
determinista con el CLI de HyperFrames (https://hyperframes.heygen.com).

Idea (la misma que manim_broll.py, pero con la web como motor gráfico): en vez
de pedirle un video a un modelo generativo, le pedimos a Gemini el *código* de
una animación y la renderizamos localmente. HyperFrames toma un `index.html`
normal y, en vez de reproducirlo, le pide al navegador un frame concreto a la
vez (`seek(0)`, `seek(1/30)`, ...) con Chrome headless en modo determinista, y
encadena los frames con ffmpeg. Nunca llama a `play()`, así que el resultado no
depende de la velocidad de la máquina: mismo HTML -> mismo MP4.

Frente a los otros dos motores:

| | veo | manim | hyperframes |
|---|---|---|---|
| Costo | de pago | gratis | gratis |
| Velocidad | minutos/clip | ~1 min/clip | ~10-20 s/clip |
| Duración del clip | fija (~8 s) | fija (~8 s) | **exacta**, la que pida la escena |
| Estilo | fotorrealista | vectorial matemático | tipografía/diseño web (kinetic type, tarjetas, datos) |

Que la duración sea exacta es la diferencia importante para este pipeline: con
veo/manim el clip se loopea para cubrir la locución y se nota el salto; acá el
clip se compone ya con la duración de la narración de esa escena.

Requiere: Node.js >= 22 (para `npx`), ffmpeg/ffprobe en el PATH.
Credenciales: GEMINI_API_KEY (el mismo del resto del pipeline). El render de
HyperFrames es local y no consume créditos de HeyGen ni pide cuenta.
"""
import os
import re
import json
import math
import shutil
import hashlib
import logging
import tempfile
import subprocess

from google import genai

import gemini_utils

# Versión fijada del CLI: HyperFrames se mueve rápido y una corrida desatendida
# no debería cambiar de motor de render sin que lo decidas. Súbela a mano.
VERSION_CLI = "0.8.29"

MODELO_TEXTO_DEFAULT = "gemini-3.6-flash"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "hyperframes_cache")

FPS = 30
TIMEOUT_RENDER_SEG = 600
TIMEOUT_LINT_SEG = 120
DURACION_DEFAULT_SEG = 8.0
# Se renderiza un poco más largo que la narración: el ajuste de duración en
# generar_video_maestro.py recorta el sobrante, y así un desfase de décimas
# nunca deja el último tramo de la escena en negro o en loop.
MARGEN_DURACION_SEG = 0.6
DURACION_MAX_SEG = 60.0

# Cambiar el prompt cambia el resultado para el mismo prompt_visual, así que
# la versión entra en la clave de caché para no reutilizar clips viejos.
VERSION_PROMPT = 1

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

_PROMPT_SISTEMA = """Eres un generador de composiciones HTML para HyperFrames, un motor
que renderiza HTML a video MP4 frame a frame con Chrome headless.

Generas el video de apoyo (b-roll) de UNA escena de un video narrado de
psicología / desarrollo personal en español. La locución va por encima en otra
pista: la composición es PURAMENTE VISUAL y MUDA.

Responde ÚNICAMENTE con el archivo HTML completo. Sin explicaciones, sin ```.

## Contrato de HyperFrames (obligatorio)

- Documento HTML completo, empezando por `<!doctype html>`.
- Carga GSAP con exactamente esta etiqueta:
  `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>`
- El elemento raíz debe ser:
  `<div id="root" data-composition-id="main" data-start="0" data-duration="{DURACION}" data-width="{ANCHO}" data-height="{ALTO}">`
  con `position: relative; width: {ANCHO}px; height: {ALTO}px; overflow: hidden;`.
- `data-duration` de la raíz vale EXACTAMENTE {DURACION}. No lo cambies.
- Cada bloque visible es una `<section class="clip" id="...">` con `data-start`
  y `data-duration` en segundos, dentro de la ventana [0, {DURACION}].
  Regla `.clip {{ position: absolute; inset: 0; }}`.
- Crea UNA sola línea de tiempo GSAP, pausada, y regístrala de forma síncrona:
  ```
  window.__timelines = window.__timelines || {{}};
  const tl = gsap.timeline({{ paused: true }});
  // ... tweens ...
  window.__timelines.main = tl;
  ```
- La animación debe durar {DURACION} segundos: encadena los tweens para llenar
  ese tiempo (usa posiciones absolutas en la timeline, p. ej. `tl.to(x, {{...}}, 2.4)`).

## Determinismo (el render pide frames sueltos, no reproduce)

- Nada de `Date`, `performance.now()`, `Math.random()` sin semilla,
  `requestAnimationFrame`, `setTimeout`, `setInterval`, `repeat: -1` ni
  animaciones CSS infinitas. El estado visual en el segundo T debe depender
  solo de T.
- Nada de `<video>`, `<audio>`, `<canvas>` con WebGL, ni imágenes externas.
- Sin peticiones de red salvo el `<script>` de GSAP indicado arriba.
- Fuentes: solo la pila del sistema
  `font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;`.

## Dirección de arte

- Fondo oscuro profundo (#080B10 - #12161D) con un degradado sutil; nada de
  blanco puro de fondo.
- Paleta de acento fría y sobria, 2 colores como máximo (p. ej. "#7C9CFF",
  "#4ADE9B", "#F2C14E"). Estilo editorial y calmado, no infantil ni "startup".
- Movimiento lento y continuo: derivas, escalas suaves, parallax, líneas que se
  dibujan, formas geométricas grandes, degradados que respiran. Easing
  `power2.out` / `power3.inOut`. Nada rebota ni parpadea.
- Nunca se queda quieto: siempre hay algo moviéndose despacio en pantalla.
- Empieza y termina en un estado compuesto (no en negro ni a medio fundido).

## Restricciones del pipeline (importantes)

- **Texto: como mucho 3 palabras en toda la composición, o ninguna.** Los
  subtítulos karaoke de la narración se queman encima después; texto de la
  composición compite con ellos y con la locución.
- **Deja libre el 25% inferior del cuadro**: ahí van los subtítulos. Nada de
  contenido importante ni elementos brillantes en esa banda.
- **Deja libre el 30% superior del cuadro**: en la primera escena va la tarjeta
  de título.
- Metáfora visual abstracta del tema, nunca ilustración literal: sin caras,
  sin figuras humanas reconocibles, sin logos ni marcas.
"""


logger = logging.getLogger("hyperframes_broll")

_client = None
_cmd_cli = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _entorno_cli():
    """Entorno para el CLI: sin telemetría ni chequeo de actualizaciones, que
    en una corrida desatendida solo añaden latencia y llamadas de red."""
    env = dict(os.environ)
    env["HYPERFRAMES_NO_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["HYPERFRAMES_NO_UPDATE_CHECK"] = "1"
    env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    env["CI"] = env.get("CI", "1")
    return env


def comando_cli():
    """Prefijo de comando del CLI de HyperFrames.

    Si hay un binario instalado (`npm i -g hyperframes`) se usa ese; si no, se
    cae a `npx`, que descarga el paquete la primera vez y luego lo cachea."""
    global _cmd_cli
    if _cmd_cli is None:
        binario = os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes")
        if binario:
            _cmd_cli = [binario]
        else:
            _cmd_cli = ["npx", "-y", f"hyperframes@{VERSION_CLI}"]
    return list(_cmd_cli)


def comprobar_dependencias():
    """Lanza si falta algo para renderizar. Se llama antes del lote para
    fallar temprano en vez de a mitad del primer video."""
    if not (os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes") or shutil.which("npx")):
        raise RuntimeError(
            "El motor 'hyperframes' necesita Node.js >= 22 (para npx) o el CLI "
            "instalado. Ver README, sección 'Motor de video de apoyo'."
        )
    faltantes = [exe for exe in ("ffmpeg", "ffprobe") if shutil.which(exe) is None]
    if faltantes:
        raise RuntimeError(
            "El motor 'hyperframes' necesita " + ", ".join(faltantes) + " en el PATH."
        )


def _ruta_cache(prompt_visual, aspecto, duracion):
    clave = hashlib.sha256(
        f"v{VERSION_PROMPT}|{aspecto}|{duracion:.1f}|{prompt_visual}".encode("utf-8")
    ).hexdigest()[:24]
    os.makedirs(CARPETA_CACHE, exist_ok=True)
    return os.path.join(CARPETA_CACHE, f"hf_{clave}.mp4")


def _archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def _limpiar_html(texto):
    """Quita las vallas de markdown que el modelo a veces añade pese al prompt."""
    texto = (texto or "").strip()
    texto = re.sub(r'^```(?:html)?\s*', '', texto)
    texto = re.sub(r'\s*```$', '', texto)
    return texto.strip()


def _generar_html(cliente, prompt_visual, modelo, duracion, w, h, correccion=None):
    sistema = _PROMPT_SISTEMA.format(DURACION=f"{duracion:.2f}", ANCHO=w, ALTO=h)
    partes = [
        sistema,
        "",
        "Idea visual de la escena (interprétala como metáfora abstracta, no la "
        f"escribas en pantalla): {prompt_visual}",
    ]
    if correccion:
        partes += [
            "",
            "El intento anterior falló. Corrige EXACTAMENTE esto y devuelve el "
            "HTML completo de nuevo:",
            correccion,
        ]

    respuesta = gemini_utils.llamar_con_reintentos(
        cliente.models.generate_content,
        model=modelo,
        contents="\n".join(partes),
    )
    html = _limpiar_html(respuesta.text or "")
    if "data-composition-id" not in html or "__timelines" not in html:
        raise ValueError("La respuesta de Gemini no es una composición de HyperFrames válida.")
    return html


def _lint(proyecto):
    """Corre el linter del CLI (sin navegador, ~1 s) y devuelve el texto de los
    errores, o None si la composición está limpia. Atrapa fallos estructurales
    antes de pagar los segundos de un render que iba a fallar igual."""
    try:
        res = subprocess.run(
            comando_cli() + ["lint", proyecto, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_LINT_SEG, env=_entorno_cli(),
        )
        salida = res.stdout or ""
        inicio = salida.find("{")
        if inicio < 0:
            return None  # sin JSON parseable: que decida el render
        datos = json.loads(salida[inicio:])
    except Exception as exc:
        logger.debug(f"lint no utilizable, se sigue al render: {exc}")
        return None

    if not datos.get("errorCount"):
        return None

    errores = []
    for f in datos.get("findings", []):
        if f.get("severity") != "error":
            continue
        linea = f"- {f.get('code', 'error')}: {f.get('message', '')}"
        # El linter trae la corrección concreta; se la pasamos tal cual al
        # modelo, que acierta mucho más que con solo el mensaje de error.
        if f.get("fixHint"):
            linea += f"\n  Cómo se arregla: {f['fixHint']}"
        errores.append(linea)
    return "El linter de HyperFrames reportó errores:\n" + "\n".join(errores[:10])


def _render(proyecto, ruta_salida):
    res = subprocess.run(
        comando_cli() + [
            "render", proyecto,
            "-o", ruta_salida,
            "--fps", str(FPS),
            "--quality", "standard",
            "--quiet",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_RENDER_SEG, env=_entorno_cli(),
    )
    if res.returncode != 0 or not _archivo_valido(ruta_salida):
        detalle = (res.stderr or res.stdout or "").strip()[-2000:]
        raise RuntimeError(f"hyperframes render falló (código {res.returncode}):\n{detalle}")


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT,
                          reintentos=3, duracion_seg=None):
    """Misma interfaz que veo_broll/manim_broll (con `duracion_seg` extra):
    devuelve la ruta local a un clip de video para el prompt dado, o None si
    falló tras los reintentos.

    A diferencia de los otros dos motores, el clip se compone con la duración
    que se pide, así que no hace falta loopearlo para cubrir la narración."""
    duracion = float(duracion_seg or DURACION_DEFAULT_SEG) + MARGEN_DURACION_SEG
    duracion = min(max(duracion, 2.0), DURACION_MAX_SEG)
    # Se redondea a medio segundo para que dos escenas de duración parecida con
    # el mismo prompt visual compartan clip en vez de renderizar dos veces.
    duracion = math.ceil(duracion * 2) / 2

    ruta_salida = _ruta_cache(prompt_visual, aspecto, duracion)
    if _archivo_valido(ruta_salida):
        return ruta_salida

    w, h = RESOLUCIONES.get(aspecto, RESOLUCIONES["16:9"])
    cliente = _obtener_cliente()
    correccion = None

    for intento in range(1, reintentos + 1):
        try:
            html = _generar_html(cliente, prompt_visual, modelo, duracion, w, h, correccion)
            with tempfile.TemporaryDirectory(prefix="hyperframes_broll_") as tmp:
                with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
                    f.write(html)

                # El linter es barato; si encuentra errores, se los devolvemos
                # al modelo en el siguiente intento en vez de gastar un render.
                errores = _lint(tmp)
                if errores:
                    raise RuntimeError(errores)

                _render(tmp, ruta_salida)

            if _archivo_valido(ruta_salida):
                return ruta_salida
        except Exception as exc:
            logger.warning(f"HyperFrames intento {intento}/{reintentos} falló: {exc}")
            correccion = str(exc)[-1500:]
            if _archivo_valido(ruta_salida):
                # Un render a medias deja un archivo inservible en la caché.
                os.remove(ruta_salida)

    return None
