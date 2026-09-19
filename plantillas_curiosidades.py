"""
Plantillas de curiosidades científicas — composiciones de HyperFrames con la
estética "panel de laboratorio nocturno" (lluvia de fondo, brillo neón,
reflejo de agua) en vez del grid oscuro de plantillas_broll.py.

Nace de un video de referencia que el usuario mostró (@1volt1amp en TikTok:
electricidad, rayos, campos eléctricos) y de un pedido explícito de investigar
cómo lograr ese nivel de producción en Python. La investigación (ver
conversación) descartó agregar librerías nuevas:

- Motores de partículas (tsParticles) usan su propio loop por
  requestAnimationFrame, prohibido por el contrato de determinismo de
  HyperFrames (rompe el modelo de "frames congelados fuera de orden"). La
  lluvia de acá son ~70 elementos DOM con su propio tween de GSAP —
  determinista, cero dependencias nuevas.
- El reflejo de agua usa `-webkit-box-reflect` (propiedad nativa de Chrome,
  que es exactamente el motor que usa HyperFrames): un reflejo que se
  mantiene sincronizado automáticamente con cualquier animación del
  contenido, sin duplicar ni un solo elemento ni tween.
- No existe ninguna librería relevante en GitHub para "rayo fractal
  recursivo" (Lichtenberg) que valga la pena vendorizar: es un random walk
  con ramificación, ~40 líneas de Python, más simple que integrar una
  dependencia externa.

Arquetipos (van entre corchetes en el VISUAL:, igual que plantillas_sello.py):
- rayo:   un rayo fractal recursivo (random walk ramificado) que se traza de
          arriba abajo — para datos de voltaje, descargas, umbrales.
- barra:  una barra de progreso con brillo y una cifra — comparación de dos
          magnitudes (ej. "en seco" vs "con lluvia").
- onda:   dos ondas senoidales superpuestas con su frecuencia rotulada —
          comparar dos fenómenos periódicos (ej. 50 Hz vs 100 Hz).
- ruido:  una señal irregular tipo osciloscopio — para fenómenos caóticos o
          "ruido" (estática, interferencia, variabilidad).

Todo el módulo es una función pura de (arquetipo, etiquetas, datos, semilla):
mismo plano, mismo dibujo, siempre — igual criterio que plantillas_broll.py.
"""
import re
import html
import random
import hashlib

logger_name = "plantillas_curiosidades"

FONDO = "#04060c"
ACENTO = "#5fc9ff"
ACENTO_CALIDO = "#ffd166"
TEXTO = "#cfe8ff"
TENUE = "#7c93ad"

TIPOGRAFIA = ("system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
              "Arial, sans-serif")

ARQUETIPOS = ("rayo", "barra", "onda", "ruido")
ARQUETIPO_DEFAULT = "barra"

_RE_ARQUETIPO = re.compile(r"\[([a-záéíóúñ]+)\]", re.I)
# Ojo: una etiqueta puede traer un número con puntos de miles ("30.000
# voltios"), así que el corte no puede excluir el punto como carácter — corta
# en "Datos:" o fin de cadena, y recién después se le saca el punto final de
# cierre de oración si lo hay.
_RE_ETIQUETAS = re.compile(r"Etiquetas:\s*(.+?)(?=\s*Datos:|$)", re.I)
_RE_DATOS = re.compile(r"Datos:\s*([0-9.,\s%-]+)", re.I)


def _esc(s):
    return html.escape(str(s), quote=True)


def parsear_plano(texto):
    """Mismo criterio tolerante que plantillas_broll.parsear_plano: un guion
    viejo o incompleto igual produce algo dibujable."""
    texto = (texto or "").strip()
    m = _RE_ARQUETIPO.search(texto)
    arquetipo = (m.group(1).lower() if m else ARQUETIPO_DEFAULT)
    arquetipo = arquetipo if arquetipo in ARQUETIPOS else ARQUETIPO_DEFAULT

    m = _RE_ETIQUETAS.search(texto)
    etiquetas = [e.strip().rstrip(".") for e in m.group(1).split(",") if e.strip()] if m else []

    m = _RE_DATOS.search(texto)
    datos = []
    if m:
        for tok in m.group(1).split(","):
            tok = tok.strip().rstrip("%").replace(" ", "")
            try:
                datos.append(float(tok))
            except ValueError:
                pass

    descripcion = _RE_ARQUETIPO.sub("", texto)
    descripcion = _RE_ETIQUETAS.sub("", descripcion)
    descripcion = _RE_DATOS.sub("", descripcion)
    descripcion = re.sub(r"\s+", " ", descripcion).strip(" .")

    return {"arquetipo": arquetipo, "etiquetas": etiquetas, "datos": datos, "descripcion": descripcion}


def _semilla(texto):
    return int(hashlib.sha256(texto.encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Capa ambiental: lluvia (GSAP puro, sin motor de partículas) + reflejo.
# ---------------------------------------------------------------------------

def _lluvia(ancho, alto, duracion, semilla, n=60):
    rng = random.Random(semilla)
    piezas, tweens = [], []
    for i in range(n):
        x = rng.uniform(0, ancho)
        length = rng.uniform(ancho * 0.02, ancho * 0.05)
        dur = rng.uniform(duracion * 0.4, duracion * 0.7)
        delay = rng.uniform(0, duracion)
        piezas.append(
            f'<div class="clip gota" id="lluvia{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x:.0f}px;top:{-length:.0f}px;width:2px;height:{length:.0f}px;'
            f'background:linear-gradient(180deg,transparent,{ACENTO}66);"></div>'
        )
        tweens.append(
            f'tl.fromTo("#lluvia{i}", {{ y: 0 }}, {{ y: {alto + length:.0f}, duration: {dur:.2f}, '
            f'ease: "none", repeat: 3, delay: {delay:.2f} }}, 0);'
        )
    return piezas, tweens


def _documento(piezas_visual, tweens_visual, ancho, alto, duracion, semilla, subtitulo=""):
    """Envuelve el arquetipo con lluvia de fondo + reflejo de agua, igual en
    todas las composiciones del formato: es la identidad visual fija que
    define el género (como el glifo fijo de plantillas_sello.py)."""
    piezas_lluvia, tweens_lluvia = _lluvia(ancho, alto, duracion, semilla)
    # La línea de agua va al 62% del alto, mismo reparto que el resto del
    # canal reserva para subtítulos/interfaz de Shorts en la mitad inferior.
    y_linea = int(alto * 0.62)

    cuerpo_visual = "\n      ".join(piezas_visual)
    cuerpo_lluvia = "\n      ".join(piezas_lluvia)
    animacion = "\n  ".join(tweens_visual + tweens_lluvia)
    sub_html = f'<div id="subtitulo">{_esc(subtitulo)}</div>' if subtitulo else ""

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<script src="gsap.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  #root {{
    position: relative; overflow: hidden;
    width: {ancho}px; height: {alto}px; background: {FONDO};
    font-family: {TIPOGRAFIA}; color: {TEXTO};
  }}
  #lluvia-capa {{ position: absolute; left: 0; top: 0; width: {ancho}px; height: {y_linea}px; overflow: hidden; }}
  #visual-capa {{
    position: absolute; left: 0; top: 0; width: {ancho}px; height: {y_linea}px;
    -webkit-box-reflect: below 16px linear-gradient(transparent, transparent 55%, rgba(200,225,255,0.16));
  }}
  #linea-agua {{ position: absolute; left: 0; top: {y_linea}px; width: {ancho}px; height: 2px; background: rgba(160,200,255,0.35); }}
  #subtitulo {{
    position: absolute; left: 0; top: {y_linea + int(alto * 0.03)}px; width: {ancho}px;
    text-align: center; padding: 0 {int(ancho * 0.08)}px; font-size: {int(ancho * 0.036)}px;
    font-weight: 700; color: {TEXTO}; text-shadow: 0 0 14px {ACENTO}99;
  }}
  .rotulo {{ font-size: {int(ancho * 0.032)}px; font-weight: 700; color: {TEXTO}; }}
  .tenue {{ color: {TENUE}; }}
</style>
</head>
<body>
<div id="root"
     data-composition-id="main" data-start="0" data-duration="{duracion}"
     data-width="{ancho}" data-height="{alto}">
  <div id="lluvia-capa">{cuerpo_lluvia}</div>
  <div id="visual-capa">{cuerpo_visual}</div>
  <div id="linea-agua"></div>
  {sub_html}
</div>
<script>
  var tl = gsap.timeline({{ paused: true }});
  {animacion}
  window.__timelines = window.__timelines || {{}};
  window.__timelines["main"] = tl;
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Arquetipos
# ---------------------------------------------------------------------------

def _rayo(plano, ancho, alto_util, duracion, semilla):
    """Rayo fractal recursivo (random walk ramificado, técnica Lichtenberg):
    un tronco principal que se abre en ramas más finas y cortas. Determinista
    por semilla — mismo plano, mismo rayo siempre."""
    rng = random.Random(semilla)
    cx = ancho // 2

    def rama(x, y, angulo, largo, grosor, profundidad, segmentos):
        if profundidad > 5 or largo < 14:
            return
        x2 = x + largo * rng.uniform(0.85, 1.15) * _sin(angulo)
        y2 = y + largo
        segmentos.append((x, y, x2, y2, grosor))
        if y2 > alto_util * 0.92:
            return
        rama(x2, y2, angulo + rng.uniform(-0.5, 0.5), largo * rng.uniform(0.75, 0.9),
             grosor * 0.8, profundidad + 1, segmentos)
        # Ramificación secundaria con probabilidad decreciente por profundidad.
        if rng.random() < 0.55 - profundidad * 0.08:
            rama(x2, y2, angulo + rng.choice([-1, 1]) * rng.uniform(0.6, 1.3),
                 largo * rng.uniform(0.45, 0.65), grosor * 0.55, profundidad + 2, segmentos)

    segmentos = []
    rama(cx, int(alto_util * 0.04), 0.0, alto_util * 0.16, max(4, int(ancho * 0.008)), 0, segmentos)

    piezas = ['<svg width="{0}" height="{1}" style="position:absolute;left:0;top:0">'.format(ancho, alto_util)]
    tweens = []
    total = len(segmentos) or 1
    for i, (x, y, x2, y2, grosor) in enumerate(segmentos):
        largo_seg = ((x2 - x) ** 2 + (y2 - y) ** 2) ** 0.5
        piezas.append(
            f'<line id="rayo{i}" class="clip" data-start="0" data-duration="{duracion}" '
            f'x1="{x:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{ACENTO_CALIDO}" stroke-width="{grosor:.1f}" stroke-linecap="round" '
            f'stroke-dasharray="{largo_seg:.1f}" stroke-dashoffset="{largo_seg:.1f}" '
            f'style="filter:drop-shadow(0 0 {grosor*2:.0f}px {ACENTO_CALIDO}aa)"/>'
        )
        t = 0.15 + (i / total) * (duracion * 0.5)
        tweens.append(
            f'tl.fromTo("#rayo{i}", {{ strokeDashoffset: {largo_seg:.1f} }}, '
            f'{{ strokeDashoffset: 0, duration: 0.18, ease: "none" }}, {t:.2f});'
        )
    piezas.append('</svg>')

    etiqueta = (plano["etiquetas"][0] if plano["etiquetas"] else plano["descripcion"][:40] or "Descarga eléctrica")
    piezas.append(
        f'<div class="clip rotulo" id="rayo-etq" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:0;top:{int(alto_util*0.86)}px;width:{ancho}px;text-align:center;'
        f'opacity:0">{_esc(etiqueta)}</div>'
    )
    tweens.append(f'tl.fromTo("#rayo-etq", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, {duracion*0.55:.2f});')
    return piezas, tweens


def _sin(rad):
    import math
    return math.sin(rad)


def _barra(plano, ancho, alto_util, duracion, semilla):
    """Barra de progreso con brillo y cifra — dos magnitudes comparadas
    (ej. 'en seco' 1 vs 'con lluvia' 6)."""
    etiquetas = plano["etiquetas"] or ["Valor"]
    datos = plano["datos"] or [1.0]
    maximo = max(datos) * 1.15 if datos else 1.0

    cy = int(alto_util * 0.5)
    barra_ancho = int(ancho * 0.78)
    barra_alto = int(alto_util * 0.09)
    x0 = (ancho - barra_ancho) // 2
    hueco = int(alto_util * 0.16)

    piezas, tweens = [], []
    n = min(len(etiquetas), len(datos)) or 1
    total_alto = n * barra_alto + (n - 1) * hueco
    y0 = cy - total_alto // 2
    for i in range(n):
        y = y0 + i * (barra_alto + hueco)
        valor = datos[i] if i < len(datos) else datos[0]
        etq = etiquetas[i] if i < len(etiquetas) else f"Valor {i+1}"
        frac = min(1.0, valor / maximo) if maximo else 0
        piezas.append(
            f'<div style="position:absolute;left:{x0}px;top:{y}px;width:{barra_ancho}px;height:{barra_alto}px;'
            f'border:1px solid {ACENTO}55;border-radius:{barra_alto}px;background:rgba(10,20,35,0.5);"></div>'
        )
        piezas.append(
            f'<div class="clip" id="barra{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x0}px;top:{y}px;height:{barra_alto}px;width:0%;'
            f'border-radius:{barra_alto}px;background:linear-gradient(90deg,{ACENTO},#bfe7ff);'
            f'box-shadow:0 0 24px 4px {ACENTO}aa;"></div>'
        )
        piezas.append(
            f'<div class="rotulo" style="position:absolute;left:{x0}px;top:{y - int(ancho*0.045)}px;'
            f'width:{barra_ancho}px;">{_esc(etq)}</div>'
        )
        t = 0.4 + i * 0.5
        tweens.append(
            f'tl.fromTo("#barra{i}", {{ width: "0%" }}, {{ width: "{frac*100:.1f}%", duration: 1.4, '
            f'ease: "power2.out" }}, {t:.2f});'
        )
    return piezas, tweens


def _onda(plano, ancho, alto_util, duracion, semilla):
    """Dos ondas senoidales superpuestas con su frecuencia rotulada."""
    import math
    etiquetas = (plano["etiquetas"] or ["Señal A", "Señal B"])[:2]
    datos = plano["datos"] or [1.0, 2.0]
    if len(datos) < 2:
        datos = datos + [datos[0] * 2 if datos else 2.0]

    colores = [ACENTO, ACENTO_CALIDO]
    cy = int(alto_util * 0.5)
    amp = int(alto_util * 0.10)
    margen = int(ancho * 0.08)
    ancho_util = ancho - 2 * margen

    piezas = [f'<svg width="{ancho}" height="{alto_util}" style="position:absolute;left:0;top:0">']
    tweens = []
    for idx, (etq, freq) in enumerate(zip(etiquetas, datos[:2])):
        ciclos = max(1.0, freq / (datos[0] or 1))
        puntos = []
        pasos = 200
        y_off = cy + (idx - 0.5) * amp * 1.6
        for p in range(pasos + 1):
            x = margen + ancho_util * p / pasos
            y = y_off + amp * math.sin(2 * math.pi * ciclos * p / pasos)
            puntos.append(f"{'M' if p == 0 else 'L'}{x:.1f},{y:.1f}")
        d = " ".join(puntos)
        color = colores[idx % len(colores)]
        piezas.append(
            f'<path id="onda{idx}" class="clip" data-start="0" data-duration="{duracion}" d="{d}" '
            f'fill="none" stroke="{color}" stroke-width="{max(6,int(ancho*0.01))}" stroke-linecap="round" '
            f'style="filter:drop-shadow(0 0 8px {color}88)"/>'
        )
        piezas.append(
            f'<text x="{margen}" y="{y_off - amp - 14}" fill="{color}" font-size="{int(ancho*0.032)}" '
            f'font-family="{TIPOGRAFIA}" font-weight="700" opacity="0" id="onda-etq{idx}">{_esc(etq)}</text>'
        )
        tweens.append(
            'var _l{0} = document.getElementById("onda{0}"); var _L{0} = _l{0}.getTotalLength();'.format(idx)
        )
        tweens.append(
            f'_l{idx}.setAttribute("stroke-dasharray", _L{idx}); _l{idx}.setAttribute("stroke-dashoffset", _L{idx});'
        )
        t = 0.3 + idx * 0.3
        tweens.append(
            f'tl.fromTo("#onda{idx}", {{ strokeDashoffset: _L{idx} }}, {{ strokeDashoffset: 0, '
            f'duration: {duracion*0.55:.2f}, ease: "power1.inOut" }}, {t:.2f});'
        )
        tweens.append(f'tl.fromTo("#onda-etq{idx}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, {t+0.2:.2f});')
    piezas.append('</svg>')
    return piezas, tweens


def _ruido(plano, ancho, alto_util, duracion, semilla):
    """Señal irregular tipo osciloscopio — fenómenos caóticos/ruido."""
    rng = random.Random(semilla)
    cy = int(alto_util * 0.5)
    amp = int(alto_util * 0.16)
    margen = int(ancho * 0.08)
    ancho_util = ancho - 2 * margen
    pasos = 90

    valor = 0.0
    puntos = []
    for p in range(pasos + 1):
        x = margen + ancho_util * p / pasos
        valor = valor * 0.6 + rng.uniform(-1, 1) * 0.4
        y = cy + amp * max(-1, min(1, valor))
        puntos.append(f"{'M' if p == 0 else 'L'}{x:.1f},{y:.1f}")
    d = " ".join(puntos)

    etiqueta = plano["etiquetas"][0] if plano["etiquetas"] else (plano["descripcion"][:40] or "Señal irregular")
    piezas = [
        f'<svg width="{ancho}" height="{alto_util}" style="position:absolute;left:0;top:0">'
        f'<path id="ruido" class="clip" data-start="0" data-duration="{duracion}" d="{d}" '
        f'fill="none" stroke="{ACENTO_CALIDO}" stroke-width="{max(4,int(ancho*0.006))}" stroke-linejoin="round" '
        f'style="filter:drop-shadow(0 0 8px {ACENTO_CALIDO}88)"/></svg>',
        f'<div class="rotulo" style="position:absolute;left:0;top:{cy+amp+20}px;width:{ancho}px;text-align:center;">'
        f'{_esc(etiqueta)}</div>',
    ]
    tweens = [
        'var _lr = document.getElementById("ruido"); var _Lr = _lr.getTotalLength();',
        '_lr.setAttribute("stroke-dasharray", _Lr); _lr.setAttribute("stroke-dashoffset", _Lr);',
        f'tl.fromTo("#ruido", {{ strokeDashoffset: _Lr }}, {{ strokeDashoffset: 0, duration: '
        f'{duracion*0.75:.2f}, ease: "none" }}, {duracion*0.1:.2f});',
    ]
    return piezas, tweens


_DIBUJANTES = {"rayo": _rayo, "barra": _barra, "onda": _onda, "ruido": _ruido}


def construir_html(prompt_visual, ancho, alto, duracion, subtitulo=""):
    """HTML completo de la composición para un plano. Sin red y sin modelo."""
    plano = parsear_plano(prompt_visual)
    semilla = _semilla(prompt_visual)
    alto_util = int(alto * 0.62)
    dibujar = _DIBUJANTES.get(plano["arquetipo"], _barra)
    piezas, tweens = dibujar(plano, ancho, alto_util, duracion, semilla)
    return _documento(piezas, tweens, ancho, alto, duracion, semilla, subtitulo=subtitulo)
