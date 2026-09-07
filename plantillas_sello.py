"""
Plantillas de sello — glifos animados por código para el formato "estoico"
(reflexiones breves sobre dolor, disciplina y templanza).

Por qué existe: el canal de referencia que inspiró este formato usa un
personaje pintado por IA que se repite igual en todo el video, superpuesto
sobre fondos que cambian — una identidad visual fija y reconocible. Pintar
ese personaje con IA cuesta dinero por imagen (Gemini/Nano Banana) y usa la
misma API que el canal dejó de usar para eso. Un glifo animado por código no
necesita ninguna API: se dibuja una vez, con GSAP, igual que ya hacemos con
los diagramas de plantillas_broll.py.

Cómo se compone sobre la foto (ver estoico_broll.py): el glifo se dibuja en
BLANCO/DORADO puro sobre fondo #000000 (negro absoluto, no el #0b0f14 de
plantillas_broll). La primera versión lo mezclaba con `blend=all_mode=screen`
(doble exposición: negro no aporta nada, la luz se suma) pero ese modo
aritmético salió roto en el ffmpeg del runner — daba magenta al "sumar" negro
puro sobre un color sólido conocido, verificado pixel a pixel. La versión
real usa la luminancia del glifo como canal alfa (`alphamerge` + `overlay`,
ver estoico_broll._mezclar): donde el glifo es negro, alfa 0, la foto queda
intacta; donde es blanco/dorado, alfa alto, se compone encima. Mismo efecto
visual buscado (el fondo negro "desaparece"), sin depender de un modo de
blend que resultó no ser confiable en este entorno.

Cada arquetipo es una idea visual mínima y sin texto (el glifo nunca compite
con los subtítulos ni con la narración, es un acompañamiento wordless):

- grieta:   una fractura cruza el cuadro y se sella con una línea dorada —
            la herida que se convierte en fuerza.
- brasa:    un punto que casi se apaga y vuelve a encenderse, más firme —
            lo que no depende de que nadie más lo sostenga.
- circulo:  un círculo que se traza completo, sin salirse — la disciplina
            como práctica repetida, no como impulso.
- anillos:  ondas concéntricas que salen de un punto — una decisión pequeña,
            un efecto que se expande.
- ascenso:  una línea vertical que sube desde abajo y remata en un destello —
            levantarse a pesar del peso.

Mismo contrato de HyperFrames que plantillas_broll.py (raíz #root con sus
data-*, elementos con class="clip", timeline pausado registrado en
window.__timelines["main"], nada de Date/Math.random/rAF/setTimeout/loops
infinitos: el determinismo es un requisito de render, no de estilo).
"""
import re
import html

logger_name = "plantillas_sello"

ARQUETIPOS_SELLO = ("grieta", "brasa", "circulo", "anillos", "ascenso")
SELLO_DEFAULT = "brasa"

ORO = "#f2c14e"
BLANCO = "#f5f3ee"

_RE_ARQUETIPO = re.compile(r"\[([a-záéíóúñ]+)\]", re.I)


def _esc(s):
    return html.escape(str(s), quote=True)


def extraer_sello_y_consulta(texto_plano):
    """De un VISUAL: "[grieta] muro agrietado luz dorada" separa el arquetipo
    del glifo (para dibujar) de las palabras de búsqueda de foto (para
    fondos_stock). Tolerante: sin tag válido, cae en SELLO_DEFAULT y usa el
    texto entero como consulta."""
    texto = (texto_plano or "").strip()
    m = _RE_ARQUETIPO.search(texto)
    sello = (m.group(1).lower() if m else SELLO_DEFAULT)
    if sello not in ARQUETIPOS_SELLO:
        sello = SELLO_DEFAULT
    consulta = _RE_ARQUETIPO.sub("", texto).strip()
    return sello, (consulta or texto)


def _documento(piezas, tweens, ancho, alto, duracion):
    cuerpo = "\n      ".join(piezas)
    animacion = "\n  ".join(tweens)
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<script src="gsap.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  #root {{
    position: relative; overflow: hidden;
    width: {ancho}px; height: {alto}px;
    background: #000000;
  }}
</style>
</head>
<body>
<div id="root"
     data-composition-id="main" data-start="0" data-duration="{duracion}"
     data-width="{ancho}" data-height="{alto}">
  {cuerpo}
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


def _grieta(ancho, alto, duracion):
    """Una fractura en zigzag cruza el cuadro y una línea dorada la sella
    de un extremo al otro."""
    cx, cy = ancho // 2, alto // 2
    amp = int(ancho * 0.09)
    puntos = [
        (cx - int(ancho * 0.30), cy - int(alto * 0.22)),
        (cx - int(ancho * 0.10), cy - int(alto * 0.06)),
        (cx + amp * 0.2, cy + int(alto * 0.04)),
        (cx - int(ancho * 0.02), cy + int(alto * 0.14)),
        (cx + int(ancho * 0.28), cy + int(alto * 0.24)),
    ]
    d = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(puntos))
    grosor = max(3, int(ancho * 0.006))
    piezas = [
        f'<svg width="{ancho}" height="{alto}" style="position:absolute;left:0;top:0">'
        f'<path id="grieta" class="clip" data-start="0" data-duration="{duracion}" d="{d}" '
        f'fill="none" stroke="{BLANCO}" stroke-width="{grosor}" stroke-linecap="round" '
        f'stroke-linejoin="round" opacity="0.85"/>'
        f'<path id="sello" class="clip" data-start="0" data-duration="{duracion}" d="{d}" '
        f'fill="none" stroke="{ORO}" stroke-width="{grosor*2}" stroke-linecap="round" '
        f'stroke-linejoin="round" style="filter:drop-shadow(0 0 {grosor*3}px {ORO})"/>'
        f'</svg>'
    ]
    tweens = [
        'var _g = document.getElementById("grieta"); var _Lg = _g.getTotalLength();',
        '_g.setAttribute("stroke-dasharray", _Lg); _g.setAttribute("stroke-dashoffset", _Lg);',
        'var _s = document.getElementById("sello"); var _Ls = _s.getTotalLength();',
        '_s.setAttribute("stroke-dasharray", _Ls); _s.setAttribute("stroke-dashoffset", _Ls);',
        f'tl.fromTo("#grieta", {{ strokeDashoffset: _Lg }}, {{ strokeDashoffset: 0, duration: '
        f'{duracion*0.35:.2f}, ease: "power1.inOut" }}, 0);',
        f'tl.fromTo("#sello", {{ strokeDashoffset: _Ls }}, {{ strokeDashoffset: 0, duration: '
        f'{duracion*0.45:.2f}, ease: "power2.inOut" }}, {duracion*0.35:.2f});',
    ]
    return piezas, tweens


def _brasa(ancho, alto, duracion):
    """Un punto que casi se apaga y vuelve a encenderse, más firme que antes."""
    cx, cy = ancho // 2, alto // 2
    r = int(min(ancho, alto) * 0.05)
    piezas = [
        f'<div class="clip" id="halo" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx - r*3}px;top:{cy - r*3}px;width:{r*6}px;height:{r*6}px;'
        f'border-radius:50%;background:radial-gradient(circle,{ORO}55,transparent 70%);"></div>',
        f'<div class="clip" id="nucleo" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx - r}px;top:{cy - r}px;width:{2*r}px;height:{2*r}px;'
        f'border-radius:50%;background:{ORO};box-shadow:0 0 {r*2}px {ORO};"></div>',
    ]
    tercio = duracion / 3
    tweens = [
        f'tl.fromTo("#nucleo", {{ opacity: 0.9, scale: 1 }}, {{ opacity: 0.25, scale: 0.55, '
        f'duration: {tercio:.2f}, ease: "sine.inOut" }}, 0);',
        f'tl.fromTo("#halo", {{ opacity: 0.8, scale: 1 }}, {{ opacity: 0.15, scale: 0.5, '
        f'duration: {tercio:.2f}, ease: "sine.inOut" }}, 0);',
        f'tl.fromTo("#nucleo", {{ opacity: 0.25, scale: 0.55 }}, {{ opacity: 1, scale: 1.25, '
        f'duration: {tercio:.2f}, ease: "power2.out" }}, {tercio:.2f});',
        f'tl.fromTo("#halo", {{ opacity: 0.15, scale: 0.5 }}, {{ opacity: 1, scale: 1.4, '
        f'duration: {tercio:.2f}, ease: "power2.out" }}, {tercio:.2f});',
        f'tl.to("#nucleo", {{ scale: 1.1, duration: {tercio:.2f}, ease: "sine.inOut" }}, {tercio*2:.2f});',
        f'tl.to("#halo", {{ scale: 1.2, duration: {tercio:.2f}, ease: "sine.inOut" }}, {tercio*2:.2f});',
    ]
    return piezas, tweens


def _circulo(ancho, alto, duracion):
    """Un círculo que se traza completo, sin salirse: la práctica repetida."""
    cx, cy = ancho // 2, alto // 2
    radio = int(min(ancho, alto) * 0.22)
    grosor = max(4, int(ancho * 0.008))
    circ = 2 * 3.141592653589793 * radio
    piezas = [
        f'<svg width="{ancho}" height="{alto}" style="position:absolute;left:0;top:0">'
        f'<circle id="anillo" class="clip" data-start="0" data-duration="{duracion}" '
        f'cx="{cx}" cy="{cy}" r="{radio}" fill="none" stroke="{BLANCO}" stroke-width="{grosor}" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})" style="filter:drop-shadow(0 0 {grosor*2}px {BLANCO}88)"/>'
        f'</svg>'
    ]
    tweens = [
        f'tl.fromTo("#anillo", {{ strokeDashoffset: {circ:.1f} }}, {{ strokeDashoffset: 0, '
        f'duration: {duracion*0.8:.2f}, ease: "power1.inOut" }}, {duracion*0.1:.2f});',
    ]
    return piezas, tweens


def _anillos(ancho, alto, duracion):
    """Ondas concéntricas que salen de un punto: una decisión pequeña, un
    efecto que se expande."""
    cx, cy = ancho // 2, alto // 2
    radio_max = int(min(ancho, alto) * 0.34)
    n = 3
    piezas = []
    tweens = []
    for i in range(n):
        grosor = max(3, int(ancho * 0.005))
        piezas.append(
            f'<div class="clip" id="onda{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{cx}px;top:{cy}px;width:0;height:0;'
            f'border-radius:50%;border:{grosor}px solid {ORO};transform:translate(-50%,-50%);opacity:0;"></div>'
        )
        t = i * (duracion / (n + 1))
        tweens.append(
            f'tl.fromTo("#onda{i}", {{ width: 0, height: 0, opacity: 0.9 }}, '
            f'{{ width: {radio_max*2}, height: {radio_max*2}, opacity: 0, duration: '
            f'{duracion*0.55:.2f}, ease: "power1.out" }}, {t:.2f});'
        )
    piezas.append(
        f'<div class="clip" id="centro" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx-6}px;top:{cy-6}px;width:12px;height:12px;'
        f'border-radius:50%;background:{ORO};box-shadow:0 0 16px {ORO};"></div>'
    )
    tweens.append(f'tl.fromTo("#centro", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3 }}, 0);')
    return piezas, tweens


def _ascenso(ancho, alto, duracion):
    """Una línea vertical sube desde abajo y remata en un destello arriba:
    levantarse a pesar del peso."""
    cx = ancho // 2
    y0, y1 = int(alto * 0.86), int(alto * 0.18)
    grosor = max(5, int(ancho * 0.01))
    piezas = [
        f'<div class="clip" id="linea" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx - grosor//2}px;top:{y1}px;width:{grosor}px;height:{y0-y1}px;'
        f'background:linear-gradient(180deg,{ORO},{ORO}22);border-radius:{grosor}px;'
        f'transform-origin:50% 100%;transform:scaleY(0);"></div>',
        f'<div class="clip" id="destello" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx-14}px;top:{y1-14}px;width:28px;height:28px;'
        f'border-radius:50%;background:{BLANCO};box-shadow:0 0 24px {BLANCO};opacity:0;"></div>',
    ]
    tweens = [
        f'tl.fromTo("#linea", {{ scaleY: 0 }}, {{ scaleY: 1, duration: {duracion*0.7:.2f}, '
        f'ease: "power2.inOut" }}, {duracion*0.1:.2f});',
        f'tl.fromTo("#destello", {{ opacity: 0, scale: 0.4 }}, {{ opacity: 1, scale: 1.2, '
        f'duration: 0.5, ease: "back.out(2)" }}, {duracion*0.75:.2f});',
        f'tl.to("#destello", {{ scale: 1, duration: {duracion*0.2:.2f}, ease: "sine.inOut" }}, '
        f'{duracion*0.8:.2f});',
    ]
    return piezas, tweens


_DIBUJANTES = {
    "grieta": _grieta,
    "brasa": _brasa,
    "circulo": _circulo,
    "anillos": _anillos,
    "ascenso": _ascenso,
}


def construir_html(sello, ancho, alto, duracion):
    """HTML completo del glifo (sin red, sin modelo). `sello` ya viene
    validado por extraer_sello_y_consulta; si no, cae en SELLO_DEFAULT."""
    sello = sello if sello in ARQUETIPOS_SELLO else SELLO_DEFAULT
    dibujar = _DIBUJANTES[sello]
    piezas, tweens = dibujar(ancho, alto, duracion)
    return _documento(piezas, tweens, ancho, alto, duracion)
