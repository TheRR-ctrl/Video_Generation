"""
Plantillas de b-roll — composiciones de HyperFrames sin llamar a ningún modelo.

Por qué: hasta ahora cada plano le pedía a Gemini que escribiera el HTML+CSS+GSAP
de la composición. Eso costaba dinero, tardaba, y fallaba de maneras que un
dibujo no debería fallar: gráficas que contradecían la narración, rótulos
inventados, cuadros vacíos, incumplimientos del contrato que había que detectar
con el linter y reintentar. Un diagrama de barras con dos etiquetas no necesita
un modelo de lenguaje: necesita que alguien lo dibuje bien una vez.

El guion ya trae todo lo que hace falta, en una línea por plano:

    VISUAL: [comparacion] (plano 2 de 3) Aparece una segunda barra...
            Etiquetas: Ansiedad, Entusiasmo. Datos: 100,0.

De ahí salen el arquetipo (qué forma dibujar), el índice del plano (cuánto
revelar) y las etiquetas y datos (qué decir). Con eso, la composición es una
función pura del texto: mismo plano, mismo dibujo, siempre.

Tres cosas que esto arregla de fondo, no solo el costo:

- **Continuidad gratis.** Los planos de una escena comparten arquetipo y
  etiquetas, así que comparten maqueta por construcción: el plano 2 es el 1 con
  un elemento más revelado. Antes cada plano se dibujaba por separado y salían
  tres láminas sueltas.
- **No hay rótulo inventado.** Las etiquetas son las del guion, literales.
- **Cumple el contrato por construcción.** No hay reintentos ni linter que
  atrape lo que el modelo hizo mal, porque el HTML lo escribe este archivo.

Contrato de HyperFrames que respetan todas las plantillas (romperlo invalida el
render): raíz con id="root", data-composition-id="main", data-start,
data-duration, data-width y data-height; cada elemento animado con class="clip"
y sus tiempos dentro del rango; gsap.min.js local, nunca un CDN; el timeline
creado pausado y registrado en window.__timelines["main"].

Determinismo: el render no reproduce el video, le pide a Chrome fotogramas
sueltos y fuera de orden, así que lo que se ve en el segundo T depende solo de
T. Nada de Date, Math.random, requestAnimationFrame, setTimeout ni repeticiones
infinitas: toda la animación vive en la timeline pausada.

Y la regla que el linter marcaba una y otra vez: nunca un `transform` en el CSS
de un elemento que GSAP anime con scale/translate/rotate, porque GSAP reescribe
el transform entero. Los valores iniciales van en el `fromTo`.
"""
import re
import html
import logging

logger = logging.getLogger("plantillas_broll")

# Paleta única para todo el canal: fondo oscuro y acentos neón, el estilo de los
# explicadores de TikTok/Shorts. Al ser fija, dos planos de la misma escena
# —y dos escenas del mismo video— no pueden salir con colores distintos.
FONDO = "#0b0f14"
TEXTO = "#e8f1f8"
TENUE = "#7c8b99"
ACENTOS = ["#00e28a", "#ff2d78", "#3da9fc", "#ffd166", "#b58cff"]
REJILLA = "rgba(255,255,255,0.05)"

TIPOGRAFIA = ("system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
              "Arial, sans-serif")

ARQUETIPOS = ("comparacion", "proporcion", "evolucion", "proceso", "estructura", "metafora")

_RE_ARQUETIPO = re.compile(r"\[([a-záéíóúñ]+)\]", re.I)
_RE_PLANO = re.compile(r"\(plano\s+(\d+)\s+de\s+(\d+)\)", re.I)
_RE_ETIQUETAS = re.compile(r"Etiquetas:\s*([^.]+?)(?:\.\s|\.$|$)", re.I)
_RE_DATOS = re.compile(r"Datos:\s*([0-9.,\s%-]+)", re.I)


def parsear_plano(texto):
    """Extrae del texto del VISUAL lo que hace falta para dibujarlo.

    Es tolerante a propósito: un guion viejo sin arquetipo, sin "(plano N de M)"
    o sin etiquetas tiene que seguir produciendo algo dibujable, no una
    excepción. Los valores que faltan caen en defaults razonables."""
    texto = (texto or "").strip()

    m = _RE_ARQUETIPO.search(texto)
    arquetipo = (m.group(1).lower() if m else "metafora")
    arquetipo = arquetipo if arquetipo in ARQUETIPOS else "metafora"

    m = _RE_PLANO.search(texto)
    indice, total = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    total = max(1, total)
    indice = min(max(1, indice), total)

    m = _RE_ETIQUETAS.search(texto)
    etiquetas = []
    if m:
        etiquetas = [e.strip() for e in m.group(1).split(",") if e.strip()]

    m = _RE_DATOS.search(texto)
    datos = []
    if m:
        for tok in m.group(1).split(","):
            tok = tok.strip().rstrip("%").replace(" ", "")
            try:
                datos.append(float(tok))
            except ValueError:
                pass

    # La descripción es lo que queda tras quitar los campos estructurados; sirve
    # de subtítulo cuando no hay mejor cosa que poner.
    descripcion = _RE_ARQUETIPO.sub("", texto)
    descripcion = _RE_PLANO.sub("", descripcion)
    descripcion = _RE_ETIQUETAS.sub("", descripcion)
    descripcion = _RE_DATOS.sub("", descripcion)
    descripcion = re.sub(r"\s+", " ", descripcion).strip(" .")

    return {
        "arquetipo": arquetipo,
        "indice": indice,
        "total": total,
        "etiquetas": etiquetas,
        "datos": datos,
        "descripcion": descripcion,
    }


def _esc(s):
    return html.escape(str(s), quote=True)


def _valores(plano, cantidad):
    """Valores a dibujar: los del guion si los hay, y si no una progresión que
    refleje el orden de las etiquetas.

    Sin "Datos:" no se inventan cifras con unidad —eso fue un error real: la
    gráfica llegó a mostrar "20%" y "95%" que nadie había dicho— sino
    magnitudes relativas sin número a la vista."""
    datos = list(plano["datos"])[:cantidad]
    if len(datos) == cantidad and any(d > 0 for d in datos):
        return datos, bool(plano["datos"])
    # Progresión suave: el último elemento es el que la escena destaca.
    return [1.0 + i * 0.9 for i in range(cantidad)], False


def _reveladas(plano, cantidad):
    """Cuántas piezas se ven en este plano.

    El plano 1 de 3 muestra la primera; el 3 de 3, todas. Así la escena avanza
    sobre el MISMO dibujo en vez de cambiar de dibujo, que era el defecto que
    hacía ver la escena como láminas sueltas."""
    if plano["total"] <= 1:
        return cantidad
    porcion = plano["indice"] / plano["total"]
    return max(1, min(cantidad, round(porcion * cantidad)))


# ---------------------------------------------------------------------------
# Andamiaje común: la caja, el estilo y el timeline. Todas las plantillas
# devuelven (piezas_html, tweens_js) y esto las envuelve en un documento que
# cumple el contrato, para que ninguna pueda romperlo por su cuenta.
# ---------------------------------------------------------------------------

def _documento(piezas, tweens, ancho, alto, alto_libre, duracion, titulo):
    cuerpo = "\n      ".join(piezas)
    animacion = "\n  ".join(tweens)
    margen_sup = int(alto_libre * 0.06)
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
    background: {FONDO};
    background-image:
      linear-gradient({REJILLA} 1px, transparent 1px),
      linear-gradient(90deg, {REJILLA} 1px, transparent 1px);
    background-size: {max(40, ancho // 12)}px {max(40, ancho // 12)}px;
    font-family: {TIPOGRAFIA}; color: {TEXTO};
  }}
  /* Zona dibujable: debajo van los subtitulos quemados y, en vertical, la
     interfaz de Shorts y TikTok. Nada visible puede salirse de acá. */
  #lienzo {{
    position: absolute; left: 0; top: 0;
    width: {ancho}px; height: {alto_libre}px;
    padding: {margen_sup}px {int(ancho * 0.07)}px;
  }}
  #titulo {{
    position: absolute; left: 0; top: {margen_sup}px; width: {ancho}px;
    text-align: center; font-size: {int(ancho * 0.052)}px; font-weight: 800;
    letter-spacing: -0.02em; padding: 0 {int(ancho * 0.07)}px; color: {TEXTO};
  }}
  .rotulo {{ font-size: {int(ancho * 0.032)}px; font-weight: 700; color: {TEXTO}; }}
  .cifra  {{ font-size: {int(ancho * 0.040)}px; font-weight: 800; }}
  .tenue  {{ color: {TENUE}; }}
</style>
</head>
<body>
<div id="root"
     data-composition-id="main" data-start="0" data-duration="{duracion}"
     data-width="{ancho}" data-height="{alto}">
  <div id="titulo" class="clip" data-start="0" data-duration="{duracion}">{_esc(titulo)}</div>
  <div id="lienzo">
      {cuerpo}
  </div>
</div>
<script>
  var tl = gsap.timeline({{ paused: true }});
  tl.fromTo("#titulo", {{ opacity: 0, y: -18 }}, {{ opacity: 1, y: 0, duration: 0.4 }}, 0);
  {animacion}
  window.__timelines = window.__timelines || {{}};
  window.__timelines["main"] = tl;
</script>
</body>
</html>
"""


def _fmt(valor, con_unidad):
    """Un número sin unidad se lee como magnitud relativa; con unidad se lee
    como un hecho. Sin "Datos:" en el guion, no se muestra número."""
    if not con_unidad:
        return ""
    entero = int(round(valor))
    return str(entero) if abs(valor - entero) < 0.05 else f"{valor:.1f}"


def _comparacion(plano, ancho, alto_libre, duracion):
    """Barras enfrentadas y rotuladas. La que la escena destaca queda más alta."""
    etiquetas = plano["etiquetas"] or ["A", "B"]
    etiquetas = etiquetas[:4]
    n = len(etiquetas)
    valores, con_unidad = _valores(plano, n)
    visibles = _reveladas(plano, n)
    maximo = max(valores) or 1.0

    # Ocupar la zona dibujable de verdad: con 0.24/0.52 las barras terminaban al
    # 76% y quedaban ~260px muertos entre ellas y el borde de la zona. El cuadro
    # se veía a medio hacer, que es la queja que ya hubo con el tercio inferior.
    zona_top = int(alto_libre * 0.20)
    zona_alto = int(alto_libre * 0.62)
    util = ancho - 2 * int(ancho * 0.07)
    hueco = int(util * 0.06)
    barra_ancho = (util - hueco * (n - 1)) // n

    piezas, tweens = [], []
    for i, etq in enumerate(etiquetas):
        x = int(ancho * 0.07) + i * (barra_ancho + hueco)
        h = max(28, int(zona_alto * (valores[i] / maximo)))
        color = ACENTOS[i % len(ACENTOS)]
        y = zona_top + zona_alto - h
        aparece = i < visibles
        cifra = _fmt(valores[i], con_unidad)
        piezas.append(
            f'<div class="clip barra" id="barra{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x}px;top:{y}px;width:{barra_ancho}px;height:{h}px;'
            f'background:linear-gradient(180deg,{color},{color}55);border-radius:{int(barra_ancho*0.12)}px;'
            f'transform-origin:50% 100%;opacity:0"></div>'
        )
        if cifra:
            piezas.append(
                f'<div class="clip cifra" id="cifra{i}" data-start="0" data-duration="{duracion}" '
                f'style="position:absolute;left:{x}px;top:{y - int(ancho*0.062)}px;width:{barra_ancho}px;'
                f'text-align:center;color:{color};opacity:0">{_esc(cifra)}</div>'
            )
        piezas.append(
            f'<div class="clip rotulo" id="rot{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x}px;top:{zona_top + zona_alto + int(ancho*0.022)}px;'
            f'width:{barra_ancho}px;text-align:center;line-height:1.2;opacity:0">{_esc(etq)}</div>'
        )
        if aparece:
            t = 0.25 + i * 0.35
            tweens.append(
                f'tl.fromTo("#barra{i}", {{ scaleY: 0, opacity: 1 }}, '
                f'{{ scaleY: 1, opacity: 1, duration: 0.55, ease: "power2.out" }}, {t:.2f});'
            )
            tweens.append(
                f'tl.fromTo("#rot{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3 }}, {t + 0.25:.2f});'
            )
            if cifra:
                tweens.append(
                    f'tl.fromTo("#cifra{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3 }}, {t + 0.4:.2f});'
                )
        else:
            # Las piezas que este plano todavía no revela quedan como hueco
            # tenue: el espectador ve que falta algo, y el plano siguiente lo
            # llena en el mismo lugar. Eso es lo que da la sensación de avance.
            tweens.append(
                f'tl.fromTo("#barra{i}", {{ scaleY: 0.06, opacity: 0 }}, '
                f'{{ scaleY: 0.06, opacity: 0.16, duration: 0.4 }}, 0.3);'
            )
            tweens.append(
                f'tl.fromTo("#rot{i}", {{ opacity: 0 }}, {{ opacity: 0.25, duration: 0.3 }}, 0.5);'
            )
    return piezas, tweens


def _proporcion(plano, ancho, alto_libre, duracion):
    """Anillo con la porción destacada. La parte crece hasta su valor y se queda."""
    etiquetas = plano["etiquetas"] or ["Parte", "Resto"]
    valores, con_unidad = _valores(plano, min(len(etiquetas), 4) or 2)
    total = sum(valores) or 1.0
    fraccion = valores[0] / total
    if plano["total"] > 1:
        fraccion *= plano["indice"] / plano["total"]

    radio = int(min(ancho * 0.36, alto_libre * 0.30))
    grosor = int(radio * 0.30)
    cx, cy = ancho // 2, int(alto_libre * 0.46)
    circ = 2 * 3.141592653589793 * radio
    color = ACENTOS[0]

    piezas = [
        f'<svg width="{ancho}" height="{alto_libre}" style="position:absolute;left:0;top:0">'
        f'<circle cx="{cx}" cy="{cy}" r="{radio}" fill="none" stroke="#1b2530" stroke-width="{grosor}"/>'
        f'<circle id="arco" class="clip" data-start="0" data-duration="{duracion}" '
        f'cx="{cx}" cy="{cy}" r="{radio}" fill="none" stroke="{color}" stroke-width="{grosor}" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
        f'</svg>',
        f'<div class="clip" id="centro" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:0;top:{cy - int(ancho*0.05)}px;width:{ancho}px;text-align:center;'
        f'font-size:{int(ancho*0.085)}px;font-weight:800;color:{color};opacity:0">'
        f'{_esc(_fmt(fraccion * 100, con_unidad) + "%" if con_unidad else "")}</div>',
        f'<div class="clip rotulo" id="pie" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:0;top:{cy + radio + int(ancho*0.05)}px;width:{ancho}px;'
        f'text-align:center;padding:0 {int(ancho*0.09)}px;opacity:0">{_esc(etiquetas[0])}</div>',
    ]
    restante = circ * (1 - fraccion)
    tweens = [
        f'tl.fromTo("#arco", {{ strokeDashoffset: {circ:.1f} }}, '
        f'{{ strokeDashoffset: {restante:.1f}, duration: 1.1, ease: "power2.inOut" }}, 0.3);',
        f'tl.fromTo("#centro", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, 0.8);',
        f'tl.fromTo("#pie", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4 }}, 0.9);',
    ]
    return piezas, tweens


def _evolucion(plano, ancho, alto_libre, duracion):
    """Línea que avanza en el tiempo con los puntos rotulados."""
    etiquetas = plano["etiquetas"] or ["Antes", "Después"]
    etiquetas = etiquetas[:5]
    n = max(2, len(etiquetas))
    valores, con_unidad = _valores(plano, n)
    visibles = _reveladas(plano, n)
    maximo, minimo = max(valores), min(valores)
    rango = (maximo - minimo) or 1.0

    izq, der = int(ancho * 0.14), ancho - int(ancho * 0.14)
    # Mismo motivo que en comparación: el trazado ocupaba el 40% central y
    # dejaba el resto de la zona dibujable vacío.
    arriba, abajo = int(alto_libre * 0.24), int(alto_libre * 0.78)
    puntos = []
    for i in range(n):
        x = izq + (der - izq) * (i / (n - 1))
        y = abajo - (abajo - arriba) * ((valores[i] - minimo) / rango)
        puntos.append((x, y))

    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(puntos))
    color = ACENTOS[2]
    piezas = [
        f'<svg width="{ancho}" height="{alto_libre}" style="position:absolute;left:0;top:0">'
        f'<line x1="{izq}" y1="{abajo}" x2="{der}" y2="{abajo}" stroke="#22303d" stroke-width="3"/>'
        f'<path id="linea" class="clip" data-start="0" data-duration="{duracion}" d="{d}" '
        f'fill="none" stroke="{color}" stroke-width="{max(9, int(ancho*0.016))}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    ]
    # El trazo se dibuja con dasharray calculado por getTotalLength en el
    # navegador: es lo que recomienda el propio componente del catálogo, y a
    # diferencia del atributo pathLength da el mismo píxel en cada seek.
    tweens = [
        'var _l = document.getElementById("linea"); var _L = _l.getTotalLength();',
        '_l.setAttribute("stroke-dasharray", _L); _l.setAttribute("stroke-dashoffset", _L);',
        f'tl.fromTo("#linea", {{ strokeDashoffset: _L }}, '
        f'{{ strokeDashoffset: _L * {1 - visibles / n:.3f}, duration: 1.3, ease: "none" }}, 0.25);',
    ]
    for i, (x, y) in enumerate(puntos):
        r = int(ancho * 0.022)
        vis = i < visibles
        piezas.append(
            f'<div class="clip" id="pt{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x - r:.0f}px;top:{y - r:.0f}px;width:{2*r}px;height:{2*r}px;'
            f'border-radius:50%;background:{color};opacity:0"></div>'
        )
        piezas.append(
            f'<div class="clip rotulo" id="etq{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x - int(ancho*0.14):.0f}px;top:{abajo + int(ancho*0.03)}px;'
            f'width:{int(ancho*0.28)}px;text-align:center;line-height:1.2;opacity:0">'
            f'{_esc(etiquetas[i]) if i < len(etiquetas) else ""}</div>'
        )
        t = 0.35 + i * (1.2 / n)
        op_pt, op_etq = (1, 1) if vis else (0.18, 0.25)
        tweens.append(f'tl.fromTo("#pt{i}", {{ opacity: 0 }}, {{ opacity: {op_pt}, duration: 0.3 }}, {t:.2f});')
        tweens.append(f'tl.fromTo("#etq{i}", {{ opacity: 0 }}, {{ opacity: {op_etq}, duration: 0.3 }}, {t + 0.1:.2f});')
    return piezas, tweens


def _proceso(plano, ancho, alto_libre, duracion):
    """Cajas rotuladas encadenadas por flechas que se dibujan una tras otra."""
    etiquetas = plano["etiquetas"] or ["Paso 1", "Paso 2"]
    etiquetas = etiquetas[:4]
    n = len(etiquetas)
    visibles = _reveladas(plano, n)

    caja_ancho = ancho - 2 * int(ancho * 0.13)
    caja_alto = int(alto_libre * 0.135)
    hueco = int((alto_libre * 0.72 - n * caja_alto) / max(1, n - 1)) if n > 1 else 0
    hueco = max(int(alto_libre * 0.035), min(hueco, int(alto_libre * 0.10)))
    total_alto = n * caja_alto + (n - 1) * hueco
    top0 = int(alto_libre * 0.22) + max(0, (int(alto_libre * 0.62) - total_alto) // 2)
    x = int(ancho * 0.13)

    piezas, tweens = [], []
    for i, etq in enumerate(etiquetas):
        y = top0 + i * (caja_alto + hueco)
        color = ACENTOS[i % len(ACENTOS)]
        vis = i < visibles
        piezas.append(
            f'<div class="clip" id="caja{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x}px;top:{y}px;width:{caja_ancho}px;height:{caja_alto}px;'
            f'border:{max(3,int(ancho*0.005))}px solid {color};border-radius:{int(caja_alto*0.22)}px;'
            f'background:{color}14;display:flex;align-items:center;justify-content:center;'
            f'padding:0 {int(ancho*0.03)}px;text-align:center;font-size:{int(ancho*0.038)}px;'
            f'font-weight:700;line-height:1.15;opacity:0">{_esc(etq)}</div>'
        )
        if i < n - 1:
            fy = y + caja_alto
            piezas.append(
                f'<svg id="flecha{i}" class="clip" data-start="0" data-duration="{duracion}" '
                f'width="{ancho}" height="{alto_libre}" '
                f'style="position:absolute;left:0;top:0;opacity:0">'
                f'<line x1="{ancho//2}" y1="{fy + 4}" x2="{ancho//2}" y2="{fy + hueco - 14}" '
                f'stroke="{TENUE}" stroke-width="4"/>'
                f'<polygon points="{ancho//2 - 11},{fy + hueco - 16} {ancho//2 + 11},{fy + hueco - 16} '
                f'{ancho//2},{fy + hueco - 2}" fill="{TENUE}"/></svg>'
            )
        t = 0.3 + i * 0.45
        tweens.append(
            f'tl.fromTo("#caja{i}", {{ opacity: 0, scale: 0.9 }}, '
            f'{{ opacity: {1 if vis else 0.2}, scale: 1, duration: 0.45, ease: "back.out(1.6)" }}, {t:.2f});'
        )
        if i < n - 1:
            tweens.append(
                f'tl.fromTo("#flecha{i}", {{ opacity: 0 }}, '
                f'{{ opacity: {1 if i < visibles - 1 else 0.2}, duration: 0.3 }}, {t + 0.3:.2f});'
            )
    return piezas, tweens


def _estructura(plano, ancho, alto_libre, duracion):
    """Un elemento central que se abre en sus partes rotuladas."""
    etiquetas = plano["etiquetas"] or ["Todo", "Parte"]
    centro_etq = etiquetas[0]
    partes = etiquetas[1:5] or ["Parte"]
    n = len(partes)
    visibles = _reveladas(plano, n)

    cx, cy = ancho // 2, int(alto_libre * 0.34)
    r_centro = int(ancho * 0.15)
    piezas = [
        f'<div class="clip" id="nucleo" data-start="0" data-duration="{duracion}" '
        f'style="position:absolute;left:{cx - r_centro}px;top:{cy - r_centro}px;'
        f'width:{2*r_centro}px;height:{2*r_centro}px;border-radius:50%;'
        f'border:{max(3,int(ancho*0.006))}px solid {ACENTOS[0]};background:{ACENTOS[0]}1f;'
        f'display:flex;align-items:center;justify-content:center;text-align:center;'
        f'padding:{int(ancho*0.02)}px;font-size:{int(ancho*0.034)}px;font-weight:800;'
        f'line-height:1.15;opacity:0">{_esc(centro_etq)}</div>'
    ]
    tweens = [
        f'tl.fromTo("#nucleo", {{ opacity: 0, scale: 0.7 }}, '
        f'{{ opacity: 1, scale: 1, duration: 0.5, ease: "back.out(1.7)" }}, 0.25);'
    ]
    fila_top = cy + r_centro + int(alto_libre * 0.08)
    util = ancho - 2 * int(ancho * 0.06)
    hueco = int(util * 0.04)
    p_ancho = (util - hueco * (n - 1)) // n
    p_alto = int(alto_libre * 0.16)
    for i, etq in enumerate(partes):
        x = int(ancho * 0.06) + i * (p_ancho + hueco)
        color = ACENTOS[(i + 1) % len(ACENTOS)]
        vis = i < visibles
        piezas.append(
            f'<svg id="rama{i}" class="clip" data-start="0" data-duration="{duracion}" '
            f'width="{ancho}" height="{alto_libre}" style="position:absolute;left:0;top:0;opacity:0">'
            f'<path d="M{cx},{cy + r_centro} C{cx},{fila_top - 30} {x + p_ancho//2},{fila_top - 40} '
            f'{x + p_ancho//2},{fila_top}" fill="none" stroke="{color}" stroke-width="3"/></svg>'
        )
        piezas.append(
            f'<div class="clip" id="parte{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x}px;top:{fila_top}px;width:{p_ancho}px;height:{p_alto}px;'
            f'border:3px solid {color};border-radius:{int(p_alto*0.18)}px;background:{color}14;'
            f'display:flex;align-items:center;justify-content:center;text-align:center;'
            f'padding:{int(ancho*0.012)}px;font-size:{int(ancho*0.030)}px;font-weight:700;'
            f'line-height:1.15;opacity:0">{_esc(etq)}</div>'
        )
        t = 0.7 + i * 0.35
        tweens.append(f'tl.fromTo("#rama{i}", {{ opacity: 0 }}, {{ opacity: {0.9 if vis else 0.15}, duration: 0.35 }}, {t:.2f});')
        tweens.append(
            f'tl.fromTo("#parte{i}", {{ opacity: 0, y: 22 }}, '
            f'{{ opacity: {1 if vis else 0.2}, y: 0, duration: 0.4, ease: "power2.out" }}, {t + 0.15:.2f});'
        )
    return piezas, tweens


def _metafora(plano, ancho, alto_libre, duracion):
    """Único arquetipo sin datos. Aun así, dos elementos y una relación visible
    entre ellos: una forma sola no comunica nada."""
    etiquetas = plano["etiquetas"] or [plano["descripcion"][:28] or "Antes", "Después"]
    if len(etiquetas) == 1:
        etiquetas = etiquetas + ["→"]
    etiquetas = etiquetas[:3]
    n = len(etiquetas)
    visibles = _reveladas(plano, n)

    cy = int(alto_libre * 0.44)
    lado = int(min(ancho * 0.30, alto_libre * 0.28))
    util = ancho - 2 * int(ancho * 0.08)
    hueco = (util - n * lado) // max(1, n - 1) if n > 1 else 0

    piezas, tweens = [], []
    for i, etq in enumerate(etiquetas):
        x = int(ancho * 0.08) + i * (lado + hueco)
        color = ACENTOS[i % len(ACENTOS)]
        vis = i < visibles
        piezas.append(
            f'<div class="clip" id="fig{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x}px;top:{cy - lado//2}px;width:{lado}px;height:{lado}px;'
            f'border-radius:{int(lado*0.22)}px;border:{max(3,int(ancho*0.006))}px solid {color};'
            f'background:{color}1f;opacity:0"></div>'
        )
        piezas.append(
            f'<div class="clip rotulo" id="figrot{i}" data-start="0" data-duration="{duracion}" '
            f'style="position:absolute;left:{x - int(ancho*0.02)}px;top:{cy + lado//2 + int(ancho*0.025)}px;'
            f'width:{lado + int(ancho*0.04)}px;text-align:center;line-height:1.2;opacity:0">{_esc(etq)}</div>'
        )
        if i < n - 1:
            fx = x + lado
            piezas.append(
                f'<svg id="rel{i}" class="clip" data-start="0" data-duration="{duracion}" '
                f'width="{ancho}" height="{alto_libre}" style="position:absolute;left:0;top:0;opacity:0">'
                f'<line x1="{fx + 10}" y1="{cy}" x2="{fx + hueco - 18}" y2="{cy}" stroke="{TENUE}" stroke-width="4"/>'
                f'<polygon points="{fx + hueco - 20},{cy - 11} {fx + hueco - 20},{cy + 11} '
                f'{fx + hueco - 4},{cy}" fill="{TENUE}"/></svg>'
            )
        t = 0.3 + i * 0.45
        tweens.append(
            f'tl.fromTo("#fig{i}", {{ opacity: 0, scale: 0.8 }}, '
            f'{{ opacity: {1 if vis else 0.2}, scale: 1, duration: 0.5, ease: "back.out(1.5)" }}, {t:.2f});'
        )
        tweens.append(f'tl.fromTo("#figrot{i}", {{ opacity: 0 }}, {{ opacity: {1 if vis else 0.25}, duration: 0.3 }}, {t + 0.25:.2f});')
        if i < n - 1:
            tweens.append(f'tl.fromTo("#rel{i}", {{ opacity: 0 }}, {{ opacity: {1 if i < visibles - 1 else 0.2}, duration: 0.3 }}, {t + 0.35:.2f});')
    return piezas, tweens


_DIBUJANTES = {
    "comparacion": _comparacion,
    "proporcion": _proporcion,
    "evolucion": _evolucion,
    "proceso": _proceso,
    "estructura": _estructura,
    "metafora": _metafora,
}


def construir_html(prompt_visual, ancho, alto, alto_libre, duracion):
    """HTML completo de la composición para un plano. Sin red y sin modelo."""
    plano = parsear_plano(prompt_visual)
    dibujar = _DIBUJANTES.get(plano["arquetipo"], _metafora)
    piezas, tweens = dibujar(plano, ancho, alto_libre, duracion)

    # El título es la primera etiqueta de la escena o, si no hay, la propia
    # descripción recortada. Siempre sale del guion: nunca un rótulo inventado.
    titulo = (plano["etiquetas"][0] if plano["etiquetas"]
              else (plano["descripcion"][:46] or ""))
    if plano["arquetipo"] == "comparacion" and len(plano["etiquetas"]) > 1:
        titulo = " vs ".join(plano["etiquetas"][:2])
    return _documento(piezas, tweens, ancho, alto, alto_libre, duracion, titulo)
