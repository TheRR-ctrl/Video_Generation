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
import json
import glob
import shutil
import hashlib
import logging
import tempfile
import subprocess

from google import genai
from google.genai import types as genai_types

import gemini_utils

MODELO_TEXTO_DEFAULT = "gemini-3.6-flash"
VERSION_CLI = "0.8.27"
# La capa gratuita de Gemini limita las solicitudes de generate_content por
# día (no solo por minuto): pedir el HTML de varias escenas en una sola
# llamada, en vez de una llamada por escena, es lo que hace viable generar
# un video completo (20+ escenas) sin agotar esa cuota. Ver TAM_LOTE_DEFAULT.
TAM_LOTE_DEFAULT = 5
CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")
CARPETA_CACHE = os.path.join(CARPETA_ESTADO, "hyperframes_cache")
RUTA_GSAP_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "gsap.min.js")
# Sub-composición del catálogo oficial de HyperFrames (registry/components/
# chart-story), vendorizada tal cual: construye una gráfica (barras/línea/
# donut/progreso) animada y determinista a partir de datos exactos, en vez de
# dejar que Gemini invente su propia animación de datos desde cero. Se ofrece
# como opción en el prompt de sistema para escenas de comparación de datos.
RUTA_CHART_STORY_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "chart-story.html")
# Igualado al largo típico de la narración de una escena (15-25s, ver
# script_writer.py). Antes eran 8s: como generar_video_maestro.py repite el
# clip con -stream_loop hasta cubrir la escena, el diagrama se armaba y se
# desarmaba dos veces por escena, con el cuadro vacío en cada corte.
DURACION_ESCENA_SEG = 18
# Momento en que el diagrama tiene que estar armado del todo (ver _PROMPT_SISTEMA):
# a partir de ahí ya nada aparece ni desaparece, para que el clip se entienda
# entrando en cualquier segundo del bucle.
_SEGUNDO_DIAGRAMA_COMPLETO = int(DURACION_ESCENA_SEG * 0.4)
# Instantes en que se comprueba que el clip muestre algo (ver
# _clip_tiene_contenido): todos posteriores a _SEGUNDO_DIAGRAMA_COMPLETO, que es
# a partir de cuándo el diagrama tiene que estar armado y sostenerse.
INSTANTES_MUESTRA_CONTENIDO = (
    DURACION_ESCENA_SEG * 0.5, DURACION_ESCENA_SEG * 0.7, DURACION_ESCENA_SEG * 0.95,
)
# Umbral de luminancia (0-255) del píxel más claro: el fondo #0b0f14 mide ~14 y
# los clips con contenido medidos arrancan en 59, así que 40 separa sin falsos
# positivos en ninguno de los dos sentidos.
LUMINANCIA_MINIMA_CONTENIDO = 40
# Generoso a propósito: la primera vez que corre en una máquina/runner nuevo,
# `npx hyperframes@version` tiene que descargar el paquete completo (incluye
# un Chromium vía Puppeteer) antes de renderizar nada. Un render en caliente
# tarda ~20-30s (ver prueba local); esto solo cubre ese arranque en frío.
TIMEOUT_RENDER_SEG = 600

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

Reglas de composición (el clip NO se ve solo: encima lleva narración y
subtítulos quemados, así que romperlas arruina el video aunque el render
funcione):
- La franja INFERIOR del cuadro (el 22% más bajo, o sea desde y={alto_libre}px
  hacia abajo) va reservada para los subtítulos: no pongas ahí NINGÚN elemento
  visible. Centrá la composición en la mitad superior.
- Todo texto en pantalla va EN ESPAÑOL y tiene que salir del contenido de la
  escena que se te describe: las palabras que la escena lista como "Etiquetas:",
  una frase corta que ya esté en esa descripción, o un número que aparezca ahí.
  Nada de rótulos decorativos inventados (nada de "MORNING FOCUS", "THE
  CROSSING", "SYNAPSE"): el espectador está escuchando otra cosa y un texto que
  no corresponde se lee como un error.
- Pero los rótulos que SÍ informan son obligatorios: si el dibujo compara, mide,
  ordena o descompone algo, cada elemento que representa una cosa lleva su
  etiqueta al lado (y su número, si la escena trae "Datos:"). Dos barras sin
  rótulo no comunican nada. Omitir texto solo es correcto cuando el dibujo no
  representa cantidades ni partes nombradas.
- No hay narración dentro del clip: el video es puramente visual, de
  {duracion} segundos.

El clip se reproduce EN BUCLE debajo de una narración más larga que él, así
que cualquier instante en que el cuadro quede vacío o a medio dibujar se ve
como un error de reproducción. Por eso:
- El primer elemento aparece dentro del primer medio segundo: nunca arranques
  con el cuadro en negro.
- El diagrama tiene que estar COMPLETO (todos sus elementos y todos sus
  rótulos a la vista) antes del 40% de la duración, o sea antes del segundo
  {segundo_completo}.
- A partir de ahí NADA desaparece: no le pongas fade-out, ni `autoAlpha: 0` al
  final, ni un `data-duration` que termine antes que la composición. Todos los
  elementos llegan visibles al último frame. El movimiento del tramo final es
  sutil (un pulso, un acento de color, una flecha que recorre el diagrama ya
  armado), nunca desarmarlo.

PRUEBA QUE TIENE QUE PASAR TU COMPOSICIÓN (es el criterio de calidad, por
encima de lo bonita que quede): alguien que ve el clip SIN audio, entrando en
CUALQUIER segundo del clip, tiene que entender la idea de la escena. Una sola forma que pulsa, gira o late; un
cuadrado de color; una línea que cruza una elipse: todo eso reprueba, es
decoración. Aprueba un dibujo con al menos dos elementos rotulados y una
relación visible entre ellos (uno más grande que otro, uno que se convierte en
otro, tres que se encadenan en ciclo, una parte destacada del total). Si lo que
se te describe te parece abstracto, tu trabajo es encontrarle la forma medible,
no dibujar la abstracción tal cual.

La escena que se te describe empieza con su arquetipo entre corchetes; dibujalo
así:
- [comparacion] -> barras enfrentadas, rotuladas. Usá `chart-story` (abajo).
- [proporcion]  -> dona o barra de progreso con la porción resaltada. `chart-story`.
- [evolucion]   -> línea que avanza en el tiempo, con el punto clave marcado. `chart-story`.
- [proceso]     -> cajas o círculos rotulados unidos por flechas que se dibujan
                   una tras otra; si es un ciclo, la última vuelve a la primera.
- [estructura]  -> un elemento central que se abre en sus partes rotuladas.
- [metafora]    -> única categoría sin datos; aun así, dos elementos y una
                   relación clara entre ellos (nunca una forma sola).

Si la escena trae "Datos:" (o es una COMPARACIÓN DE DATOS/NÚMEROS: tamaños,
distancias, temperaturas, duraciones, cantidades — p. ej. "la Tierra cabe 1300
veces dentro de Júpiter"), NO inventes tu propia gráfica animada: usá la
sub-composición ya construida `chart-story.html` (disponible en el mismo
directorio que tu HTML), pasándole esos números y esas etiquetas tal cual, así:

    <div id="grafica" data-composition-id="chart-story"
         data-composition-src="chart-story.html"
         data-variable-values='{{"type":"bars","data":"1,1300","labels":"Tierra,Júpiter","emphasize":1,"unit":"x","accent":"blue"}}'
         data-start="0" data-duration="{duracion}" data-track-index="0"
         data-width="{ancho}" data-height="{alto}"></div>

Ese `<div>` va DENTRO de tu `#root` normal (junto a cualquier otro elemento
de la escena). `type` puede ser "bars", "line", "donut" o "progress"; `data`
son los números reales separados por coma (se muestran exactos, sin
redondear); `emphasize` es el índice del dato a resaltar; `accent` es
"green", "blue" o "violet". No declares tú mismo un timeline para esta
sub-composición ni le pongas `class="clip"` — ella ya trae su propia
animación y su propio registro en window.__timelines["chart-story"].

`labels` son exactamente las etiquetas que la escena te dio (en español, en el
mismo orden que los números) y `data` los números tal cual: no los redondees ni
los sustituyas por valores propios. Si la escena da etiquetas pero no números,
podés usar `chart-story` igual con magnitudes relativas que reflejen lo que dice
la narración (p. ej. "3,1" para "pesa el triple"), o dibujar el diagrama vos
mismo — pero rotulado.
"""

_PROMPT_SISTEMA_LOTE = _PROMPT_SISTEMA + """
Vas a generar {n} composiciones distintas, una por cada escena listada abajo
(cada una es un video independiente, no una sola composición larga).

Responde ÚNICAMENTE con un array JSON de exactamente {n} strings, en el
mismo orden que las escenas. Cada string es el HTML completo de una
composición (empezando literalmente en "<!doctype html>"). Sin texto antes
o después del array, sin markdown.
"""


logger = logging.getLogger("hyperframes_broll")

_client = None


def _obtener_cliente():
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


def _ruta_cache(prompt_visual, aspecto):
    # La duración entra en la clave: un clip cacheado con otra duración ya no
    # sirve (se armó para un bucle distinto), y sin esto la caché entre
    # corridas lo revive silenciosamente.
    clave = hashlib.sha256(
        f"{aspecto}|{DURACION_ESCENA_SEG}|{prompt_visual}".encode("utf-8")
    ).hexdigest()[:24]
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
    instrucciones = _PROMPT_SISTEMA.format(
        duracion=DURACION_ESCENA_SEG, ancho=ancho, alto=alto, alto_libre=int(alto * 0.78),
        segundo_completo=_SEGUNDO_DIAGRAMA_COMPLETO,
    )
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


def _generar_composiciones_lote(cliente, prompts_visuales, aspecto, modelo):
    ancho, alto = RESOLUCIONES.get(aspecto, RESOLUCIONES["16:9"])
    n = len(prompts_visuales)
    instrucciones = _PROMPT_SISTEMA_LOTE.format(
        duracion=DURACION_ESCENA_SEG, ancho=ancho, alto=alto, alto_libre=int(alto * 0.78),
        segundo_completo=_SEGUNDO_DIAGRAMA_COMPLETO, n=n,
    )
    lista_escenas = "\n".join(
        f"{i}. {p} (no la copies literal, interprétala visualmente)"
        for i, p in enumerate(prompts_visuales, 1)
    )
    respuesta = gemini_utils.llamar_con_reintentos(
        cliente.models.generate_content,
        model=modelo,
        contents=f"{instrucciones}\n\nEscenas:\n{lista_escenas}",
        config=genai_types.GenerateContentConfig(
            # Con solo response_mime_type, Gemini a veces devuelve JSON mal
            # formado en respuestas largas (un array de 5 documentos HTML
            # completos) y se pierde la llamada entera, carísimo con la
            # cuota tan ajustada. response_schema fuerza decodificación
            # restringida a un array de strings válido.
            response_mime_type="application/json",
            response_schema=list[str],
        ),
    )
    datos = json.loads(respuesta.text or "[]")
    if not isinstance(datos, list) or len(datos) != n:
        raise ValueError(f"Se esperaban {n} composiciones en el array JSON, llegaron {datos if not isinstance(datos, list) else len(datos)}.")

    htmls = [_limpiar_html(h) for h in datos]
    for html in htmls:
        if "id=\"root\"" not in html or "__timelines" not in html:
            raise ValueError("Una composición del lote no cumple el contrato de HyperFrames.")
    return htmls


def _clip_tiene_contenido(ruta_clip):
    """True si el clip muestra algo sobre el fondo en los instantes en que el
    diagrama ya debería estar armado.

    Existe porque un render puede terminar con código 0 y un mp4 válido, y aun
    así ser 18 segundos de fondo liso (una composición donde nada llegó a
    dibujarse). Eso pasa desapercibido hasta que se ve el video terminado, con
    la escena entera en negro debajo de la narración. Acá se detecta y se trata
    como un render fallido, para que el reintento pida otra composición.

    Mide el píxel más claro del 78% superior del cuadro (el resto va tapado por
    los subtítulos): el fondo es #0b0f14, o sea luminancia ~14, y los clips
    buenos medidos llegan a 59 o más."""
    for instante in INSTANTES_MUESTRA_CONTENIDO:
        try:
            res = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{instante:.2f}", "-i", ruta_clip,
                 "-frames:v", "1", "-vf", "crop=iw:ih*0.78:0:0,scale=64:36",
                 "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=60,
            )
        except Exception as exc:
            logger.warning(f"No se pudo inspeccionar {ruta_clip}: {exc}")
            return True  # ante la duda, no descartes un clip que quizá esté bien
        if res.stdout and max(res.stdout) >= LUMINANCIA_MINIMA_CONTENIDO:
            return True
    return False


def _clip_cacheado_utilizable(ruta_clip):
    """Un clip de la caché sirve solo si además de existir muestra algo. La
    caché sobrevive entre corridas (ver el workflow), así que sin esto un clip
    que salió vacío se reusaría para siempre: la validación del render nunca
    volvería a correr sobre él. Cuando no sirve se borra, y la escena se
    regenera en esta misma corrida."""
    if not _archivo_valido(ruta_clip):
        return False
    if _clip_tiene_contenido(ruta_clip):
        return True

    logger.warning(f"Clip cacheado vacío, se descarta y se regenera: {ruta_clip}")
    try:
        os.remove(ruta_clip)
    except OSError as exc:
        logger.warning(f"No se pudo borrar el clip vacío {ruta_clip}: {exc}")
    return False


def _renderizar_composicion(html, ruta_salida):
    if not _archivo_valido(RUTA_GSAP_VENDOR):
        raise RuntimeError(f"No se encontró {RUTA_GSAP_VENDOR} (gsap.min.js vendorizado).")

    with tempfile.TemporaryDirectory(prefix="hyperframes_broll_") as tmp:
        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        shutil.copyfile(RUTA_GSAP_VENDOR, os.path.join(tmp, "gsap.min.js"))
        if _archivo_valido(RUTA_CHART_STORY_VENDOR):
            shutil.copyfile(RUTA_CHART_STORY_VENDOR, os.path.join(tmp, "chart-story.html"))
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

        ruta_render = max(candidatos, key=os.path.getmtime)
        if not _clip_tiene_contenido(ruta_render):
            raise RuntimeError(
                "El clip renderizado quedó vacío (el cuadro no muestra nada sobre el "
                "fondo). Se descarta para que el reintento genere otra composición."
            )
        shutil.copyfile(ruta_render, ruta_salida)


def generar_clip_cacheado(prompt_visual, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT, reintentos=2):
    """Misma interfaz que veo_broll/manim_broll.generar_clip_cacheado: devuelve
    la ruta local a un clip de video para el prompt dado (generado con
    HyperFrames), o None si falló tras los reintentos."""
    ruta_salida = _ruta_cache(prompt_visual, aspecto)
    if _clip_cacheado_utilizable(ruta_salida):
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


def generar_clips_lote_cacheados(prompts_visuales, aspecto="16:9", modelo=MODELO_TEXTO_DEFAULT,
                                  tam_lote=TAM_LOTE_DEFAULT, reintentos=2):
    """Genera clips para una lista de prompts (una por escena), agrupando las
    llamadas a Gemini de a `tam_lote` escenas por solicitud en vez de una por
    escena. Devuelve una lista de rutas alineada con prompts_visuales (None
    en las posiciones que fallaron tras los reintentos).

    Cada reintento solo vuelve a pedir las escenas que aún faltan (ya sea
    porque el lote completo falló, o porque el render de una escena puntual
    del lote falló) — así un fallo aislado no gasta una llamada extra en
    escenas que ya salieron bien."""
    rutas = [None] * len(prompts_visuales)
    for i, prompt in enumerate(prompts_visuales):
        ruta = _ruta_cache(prompt, aspecto)
        if _clip_cacheado_utilizable(ruta):
            rutas[i] = ruta

    cliente = _obtener_cliente()
    for intento in range(1, reintentos + 1):
        pendientes = [(i, p) for i, p in enumerate(prompts_visuales) if rutas[i] is None]
        if not pendientes:
            break

        for inicio in range(0, len(pendientes), tam_lote):
            lote = pendientes[inicio:inicio + tam_lote]
            indices, prompts_lote = zip(*lote)
            try:
                htmls = _generar_composiciones_lote(cliente, list(prompts_lote), aspecto, modelo)
            except Exception as exc:
                logger.warning(f"Lote HyperFrames (intento {intento}/{reintentos}, escenas {list(indices)}) falló al generar HTML: {exc}")
                continue

            for idx, prompt, html in zip(indices, prompts_lote, htmls):
                ruta_salida = _ruta_cache(prompt, aspecto)
                try:
                    _renderizar_composicion(html, ruta_salida)
                    if _archivo_valido(ruta_salida):
                        rutas[idx] = ruta_salida
                except Exception as exc:
                    logger.warning(f"Render de la escena {idx} (lote) falló: {exc}")

    return rutas
