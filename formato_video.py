"""
Formato de video — el tiempo decide la perspectiva.

Regla tomada de video-scout-pipeline, que ya resolvió esto (ver ahí
`generar_video_maestro.py`, `es_short = (dur_sec <= DURACION_MAX_SHORT_SEC)`):
el formato no es una preferencia que se configura aparte, es una consecuencia de
cuánto dura el video. Hasta el umbral, es un short y vive en vertical; pasado el
umbral, es un video largo y vive en horizontal. La perspectiva no es estética:
es dónde se ve. El feed de Shorts y TikTok es vertical y recorta cualquier otra
cosa; un video largo se ve en pantalla completa, que es horizontal.

El problema que resuelve acá: `formato`, `aspecto_video` y
`duracion_min/max_video_seg` eran tres ajustes independientes de config.json que
había que editar a mano en conjunto y que nada obligaba a estar de acuerdo. Un
`formato: largo` con el `aspecto_video: 9:16` que había quedado del short
anterior producía un video de diez minutos en vertical, y el pipeline lo
renderizaba entero sin decir nada: el error recién se veía en el archivo
terminado, media hora de render después.

Hay dos momentos en que hace falta saber el formato, y son distintos:

- **Antes de que el video exista** (plan, guion, y el render del b-roll, que en
  este repo se genera antes de la locución). Ahí no hay duración real todavía,
  así que se usa la duración OBJETIVO de config.json. Es lo que hace también el
  script_writer del repo hermano al pedir "un video de varios minutos, no un
  short".
- **Cuando el archivo ya está hecho** (publisher). Ahí sí hay duración real
  medida, y es la que manda: `es_short_medido()`. Un video que apuntaba a 60s y
  salió de 200 es un video largo, diga lo que diga config.json.

El umbral es el mismo que el del repo hermano: 180s. YouTube Shorts admite hasta
tres minutos, así que 60 dejaría fuera del formato vertical a videos que la
plataforma sí trata como Short.
"""
import logging

logger = logging.getLogger("formato_video")

# Varias funciones de acá consultan el formato, así que una sola incoherencia de
# config.json se avisaría cuatro o cinco veces por video. Se dice una vez.
_avisados = set()


def _avisar(mensaje):
    if mensaje not in _avisados:
        _avisados.add(mensaje)
        logger.warning(mensaje)

FORMATO_SHORT = "short"
FORMATO_LARGO = "largo"

# Mismo valor y misma regla que DURACION_MAX_SHORT_SEC en video-scout-pipeline.
# Se puede pisar desde config.json con "duracion_max_short_seg".
DURACION_MAX_SHORT_SEG = 180.0

ASPECTO_POR_FORMATO = {
    FORMATO_SHORT: "9:16",
    FORMATO_LARGO: "16:9",
}

RESOLUCION_POR_FORMATO = {
    FORMATO_SHORT: (1080, 1920),
    FORMATO_LARGO: (1920, 1080),
}

# Ventana de duración objetivo razonable para cada formato, para avisar cuando
# la de config.json no se corresponde con el formato declarado.
DURACION_POR_FORMATO = {
    FORMATO_SHORT: (15, DURACION_MAX_SHORT_SEG),
    FORMATO_LARGO: (DURACION_MAX_SHORT_SEG, 20 * 60),
}


def umbral_short(cfg):
    valor = (cfg or {}).get("duracion_max_short_seg")
    return float(valor) if isinstance(valor, (int, float)) and valor > 0 else DURACION_MAX_SHORT_SEG


def es_short_medido(duracion_seg, cfg=None):
    """La regla del repo hermano, aplicada a una duración REAL ya medida.

    Es la única versión que no puede equivocarse, porque no depende de lo que
    alguien haya escrito en config.json sino de lo que dura el archivo."""
    return float(duracion_seg) <= umbral_short(cfg)


def formato_de(cfg):
    """Formato objetivo, para cuando el video todavía no existe.

    Sale de la duración objetivo de config.json con la misma regla del umbral.
    Un `formato` declarado explícitamente gana, porque es la forma de pedir un
    formato antes de haber fijado las duraciones; si contradice a la duración,
    se avisa."""
    dur_max = (cfg or {}).get("duracion_max_video_seg")
    por_tiempo = None
    if isinstance(dur_max, (int, float)) and dur_max > 0:
        por_tiempo = FORMATO_SHORT if es_short_medido(dur_max, cfg) else FORMATO_LARGO

    declarado = (cfg or {}).get("formato")
    if declarado in ASPECTO_POR_FORMATO:
        if por_tiempo and por_tiempo != declarado:
            _avisar(
                f"config.json declara formato '{declarado}' pero pide videos de "
                f"hasta {dur_max}s, que es formato '{por_tiempo}' (umbral "
                f"{umbral_short(cfg):.0f}s). Se respeta '{declarado}'; revisá las duraciones."
            )
        return declarado

    if declarado:
        _avisar(f"formato '{declarado}' desconocido; se deduce de la duración.")
    return por_tiempo or FORMATO_LARGO


def es_short(cfg):
    return formato_de(cfg) == FORMATO_SHORT


def aspecto_de(cfg):
    """Perspectiva que corresponde al formato.

    Si config.json trae un `aspecto_video` distinto, se avisa y se ignora: es
    casi siempre un valor que quedó del formato anterior, y renderizar media
    hora en la perspectiva equivocada cuesta mucho más que este aviso."""
    formato = formato_de(cfg)
    derivado = ASPECTO_POR_FORMATO[formato]
    declarado = (cfg or {}).get("aspecto_video")
    if declarado and declarado != derivado:
        _avisar(
            f"config.json pide aspecto_video '{declarado}' pero el formato es "
            f"'{formato}': se usa '{derivado}'. La perspectiva sale del tiempo, "
            f"no se configura aparte."
        )
    return derivado


def resolucion_de(cfg):
    return RESOLUCION_POR_FORMATO[formato_de(cfg)]


def limites_duracion(cfg):
    """(min, max) en segundos para este formato, tomados de config.json cuando
    son coherentes con él y corregidos —avisando— cuando no lo son.

    Un `formato: short` con la ventana de 4 a 20 minutos que quedó del formato
    largo hace que el guionista escriba diez minutos de locución para un video
    que el feed corta a los tres."""
    formato = formato_de(cfg)
    piso, techo = DURACION_POR_FORMATO[formato]
    dur_min = (cfg or {}).get("duracion_min_video_seg")
    dur_max = (cfg or {}).get("duracion_max_video_seg")

    coherente = (
        isinstance(dur_min, (int, float)) and isinstance(dur_max, (int, float))
        and 0 < dur_min <= dur_max
        and piso <= dur_min and dur_max <= techo
    )
    if coherente:
        return dur_min, dur_max

    if dur_min is not None or dur_max is not None:
        _avisar(
            f"La duración de config.json ({dur_min}-{dur_max}s) no corresponde al "
            f"formato '{formato}': se usa {piso}-{techo}s."
        )
    return piso, techo
