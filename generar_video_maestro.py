"""
Generar Video Maestro — renderiza cada día del guion (guion.txt, salida de
script_writer.py) como un video narrado con voz IA (Gemini TTS) sobre video
de apoyo generado por IA (Veo), subtítulos karaoke y música de fondo
opcional.

A diferencia del pipeline hermano video-scout-pipeline (que arma el fondo
cortando videoclips propios), acá cada escena del guion genera su propio
clip de video con Veo a partir del prompt_visual escrito por script_writer.py,
ajustado (loop/recorte) a la duración real de su narración.

Salida: pipeline_state/resultado_lote.json (uno por corrida, describe los
videos completados y los fallidos).

Requiere: ffmpeg/ffprobe en el PATH, pip install -r requirements.txt.
"""
import os
import re
import sys
import json
import math
import shutil
import random
import logging
import subprocess
import tempfile
from datetime import timedelta

import env_local  # noqa: F401 (carga .env si existe)
import tts_gemini
import tts_edge
import veo_broll
import manim_broll
import hyperframes_broll
import hyperframes_audio_mix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_RESULTADO = os.path.join(CARPETA_ESTADO, "resultado_lote.json")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULT = {
    "carpeta_salida": os.path.join(os.path.expanduser("~"), "Desktop", "Videos Creados"),
    "voz_masculina": "Charon",
    "voz_femenina": "Kore",
    "genero_narrador": "masculino",
    "aspecto_video": "16:9",
    "modelo_tts": "gemini-2.5-flash-preview-tts",
    "modelo_veo": "veo-3.0-generate-001",
    "modelo_texto": "gemini-3.6-flash",
    "motor_broll": "veo",
    "motor_tts": "gemini",
    "voz_masculina_edge": tts_edge.VOZ_FALLBACK_MASCULINA,
    "voz_femenina_edge": tts_edge.VOZ_FALLBACK_FEMENINA,
    "reintentar_existentes": False,
    "ducking_hyperframes": True,
    "fuerza_carve_musica": hyperframes_audio_mix.FUERZA_CARVE_DEFAULT,
}

RESOLUCIONES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}

DURACION_INTRO_CARD_SEG = 3.0
# En un short de 40 segundos, 3 de tarjeta de título son el 8% del video y —peor—
# retrasan el hook, que es justo lo que decide si el espectador se queda. El
# formato corto arranca directo en la primera palabra.
DURACION_INTRO_CARD_SHORT_SEG = 0.0
FPS_VIDEO = 30

# Reglas de subtitulado. Agrupar de a N palabras fijas dejaba cada bloque 1.4s
# en pantalla (medido sobre el video terminado: 43 bloques por minuto) y partía
# las frases a mitad de camino. Estos tres valores son el criterio de siempre:
# una línea que entre completa, un piso de tiempo para poder leerla, y un techo
# para que no se quede pegada.
CARACTERES_MAX_SUBTITULO = 26
SEGUNDOS_MIN_SUBTITULO = 1.0
# Cuánto texto entra en UNA línea, que no es lo mismo que cuánto texto lleva un
# bloque: el bloque apunta a 26 caracteres (3-5 palabras, como la referencia),
# pero cuando queda algo más largo —porque partirlo daría un subtítulo de medio
# segundo— igual entra en una sola línea. Con la Montserrat Black condensada al
# 88% y cuerpo 64, 40 caracteres ocupan ~1500px de los 1800 disponibles.
CARACTERES_MAX_LINEA = 40
# En vertical el cuadro tiene 1080px de ancho en vez de 1920: entra bastante
# menos texto por línea, y el bloque se acorta en la misma proporción.
CARACTERES_MAX_SUBTITULO_VERTICAL = 20
CARACTERES_MAX_LINEA_VERTICAL = 24


SEGUNDOS_MAX_SUBTITULO = 6.0
PUNTUACION_FUERTE = (".", "?", "!", "…", ":")
PUNTUACION_DEBIL = (",", ";")
# Adelanto del subtítulo respecto de la voz, copiado de video-scout-pipeline.
ADELANTO_SUBTITULO = timedelta(seconds=0.32)
# Estilo del subtítulo, calcado del video de referencia que pasó el usuario
# (docs/referencia-subtitulos.jpg): la frase en blanco con contorno negro
# grueso, y la palabra que se está diciendo más grande. Además, las palabras con
# peso propio se encienden en color, rotando por una paleta fija.
COLOR_BASE = "&H00FFFFFF&"
# Los cuatro acentos, medidos sobre los píxeles del video de referencia y
# pasados a formato ASS (&HBBGGRR): verde (48,200,128), cian (56,200,224),
# amarillo (245,220,70) y rojo (245,110,100). Rotan en ese orden.
PALETA_RESALTADO = ("&H0080C830&", "&H00E0C838&", "&H0046DCF5&", "&H00646EF5&")
# Solo las palabras de este largo para arriba reciben color; las cortas —de, la,
# mi, un— solo crecen y siguen en blanco. La regla sale de mirar la referencia
# cuadro a cuadro: HERMANITOS, ESTABAN, DORMIDOS y PUERTA van en color, mientras
# que BIEN, MAMÁ, TOCÓ, MI y DE se quedan en blanco.
LARGO_MIN_PALABRA_COLOREADA = 5
# Tipografía condensada: la referencia usa una itálica pesada y angosta. No hay
# una así en los repos de Ubuntu (Anton, Oswald), así que se consigue el mismo
# efecto estrechando Montserrat Black, que ya se instala en el workflow.
ESCALA_BASE_X = 88
FACTOR_RESALTADO = 1.35

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("video_maestro")


def configurar_ancho_subtitulos(w, h):
    """Fija los dos límites de ancho del subtítulo según el formato del video.

    Son globales del módulo y no parámetros porque los usa toda la cadena de
    agrupado (el corte de bloque, el partido de cues largos y el salto de línea),
    y enhebrarlos por cinco funciones para un valor que no cambia dentro de una
    corrida ensucia más de lo que aclara. Se llama una vez, al empezar el video."""
    global CARACTERES_MAX_SUBTITULO, CARACTERES_MAX_LINEA
    if h > w:
        CARACTERES_MAX_SUBTITULO = CARACTERES_MAX_SUBTITULO_VERTICAL
        CARACTERES_MAX_LINEA = CARACTERES_MAX_LINEA_VERTICAL


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        try:
            with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as exc:
            logger.warning(f"No se pudo leer {RUTA_CONFIG}, usando valores por defecto: {exc}")
    return cfg


# ---------------------------------------------------------
# UTILIDADES DE PROCESO Y ARCHIVOS
# ---------------------------------------------------------
def ejecutar_comando(cmd, descripcion="Comando", timeout=None, check=True):
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{descripcion}: tiempo de espera agotado.") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"{descripcion}: no se encontró el ejecutable '{cmd[0]}'.") from exc

    if check and res.returncode != 0:
        detalle = (res.stderr or res.stdout or "").strip()
        if len(detalle) > 2500:
            detalle = detalle[-2500:]
        raise RuntimeError(f"{descripcion} falló (código {res.returncode}).\n{detalle}")
    return res


def archivo_valido(ruta):
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0


def comprobar_dependencias():
    faltantes = [exe for exe in ("ffmpeg", "ffprobe") if shutil.which(exe) is None]
    if faltantes:
        raise RuntimeError("Faltan dependencias externas: " + ", ".join(faltantes))


class GestorTemporales:
    def __init__(self, prefijo="video_maestro_"):
        self._tmp = tempfile.TemporaryDirectory(prefix=prefijo)
        self.directorio = self._tmp.name

    def registrar(self, ruta):
        ruta = str(ruta)
        if not os.path.isabs(ruta):
            ruta = os.path.join(self.directorio, ruta)
        return ruta

    def limpiar(self):
        self._tmp.cleanup()


def medir_duracion_media(ruta_archivo):
    try:
        if not archivo_valido(ruta_archivo):
            return 0.0
        res = ejecutar_comando(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", ruta_archivo],
            "ffprobe", timeout=15,
        )
        return max(0.0, float(json.loads(res.stdout)["format"]["duration"]))
    except Exception:
        return 0.0


def limpiar_texto_seguro(texto):
    if not texto:
        return "Video sin texto"
    t = re.sub(r'[\"\'«»""'']', '', texto.replace('\r', ' ').replace('\n', ' '))
    t = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚüÜñÑ0-9\s\.,;:¿?¡!()-]', '', t)
    return ' '.join(t.split()) or "Video sin texto"


# ---------------------------------------------------------
# TARJETA DE INTRO (Pillow)
# ---------------------------------------------------------
def obtener_fuente_bold(tamano=46):
    from PIL import ImageFont
    rutas_preferidas = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        "C:\\Windows\\Fonts\\impact.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for r in rutas_preferidas:
        if os.path.exists(r):
            try:
                return ImageFont.truetype(r, tamano)
            except Exception:
                pass
    return ImageFont.load_default()


def crear_tarjeta_intro(titulo, output_png, ancho, alto):
    from PIL import Image, ImageDraw
    import textwrap

    lienzo = Image.new('RGBA', (ancho, alto), (0, 0, 0, 0))
    draw = ImageDraw.Draw(lienzo)
    card_w = int(ancho * 0.8)
    card_h = int(alto * 0.22)
    card_x, card_y = (ancho - card_w) // 2, int(alto * 0.08)

    draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=24, fill=(0, 0, 0, 190))

    titulo_mayus = titulo.upper()
    chars_linea = max(24, int(len(titulo_mayus) / 3.2))
    font_tit = obtener_fuente_bold(52)
    draw.multiline_text(
        (ancho // 2, card_y + card_h // 2), "\n".join(textwrap.wrap(titulo_mayus, width=chars_linea)),
        fill=(255, 255, 255), font=font_tit, anchor="mm", align="center", spacing=10,
        stroke_width=3, stroke_fill=(0, 0, 0),
    )
    lienzo.save(output_png)


# ---------------------------------------------------------
# SUBTÍTULOS KARAOKE (SRT -> ASS)
# ---------------------------------------------------------
def formatear_srt_time(segundos):
    """Formatea segundos como HH:MM:SS,mmm.

    Se redondea a milisegundos ENTEROS antes de partir el valor. Redondear
    después, sobre la parte decimal sola, desbordaba: 3.9996 daba "00:00:03,1000"
    —cuatro dígitos de milisegundo, un timestamp inválido— y al releerlo daba
    3.1s, casi un segundo hacia atrás. En el video eso se veía como subtítulos
    que aparecían un instante y desaparecían, o que se adelantaban a la voz."""
    total_ms = max(0, int(round(segundos * 1000)))
    horas, resto = divmod(total_ms, 3600_000)
    minutos, resto = divmod(resto, 60_000)
    segs, ms = divmod(resto, 1000)
    return f"{horas:02d}:{minutos:02d}:{segs:02d},{ms:03d}"


def _cierra_bloque(texto_acumulado, texto_siguiente, duracion):
    """Decide si el bloque acumulado se cierra antes de sumar la palabra
    siguiente. El orden de las reglas es el que hace que los cortes caigan
    donde caen las pausas al hablar."""
    chars = len(texto_acumulado)
    if chars + 1 + len(texto_siguiente) > CARACTERES_MAX_SUBTITULO:
        return True  # no entra en una línea legible
    if duracion >= SEGUNDOS_MAX_SUBTITULO:
        return True  # ya lleva demasiado en pantalla
    if duracion < SEGUNDOS_MIN_SUBTITULO:
        return False  # nunca cortes antes del mínimo legible
    if texto_acumulado.endswith(PUNTUACION_FUERTE):
        return True  # fin de oración: el corte natural
    if texto_acumulado.endswith(PUNTUACION_DEBIL) and chars >= CARACTERES_MAX_SUBTITULO * 0.6:
        return True  # coma o punto y coma con el bloque ya bien lleno
    return False


def leer_bloques_srt_escena(ruta_srt, offset_seg, indice_inicial):
    """Reusa el SRT que edge-tts escribió para esta escena (marcas de tiempo
    reales por palabra) desplazándolo al lugar que ocupa la escena dentro del
    video completo.

    Es la diferencia entre un karaoke que sigue a la voz y uno que va a la
    deriva: la alternativa (generar_bloques_srt_escena) reparte el tiempo por
    cantidad de caracteres, lo que se desfasa apenas la locución cambia de
    ritmo. Devuelve ([], indice_inicial) si el SRT no existe o viene vacío, para
    que el llamador pueda caer a la estimación sin romperse."""
    if not archivo_valido(ruta_srt):
        return [], indice_inicial

    with open(ruta_srt, "r", encoding="utf-8") as f:
        contenido = f.read()

    bloques = re.findall(
        r"\d+\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)",
        contenido, re.DOTALL,
    )
    if not bloques:
        return [], indice_inicial

    cues = []
    for t_ini_str, t_fin_str, texto in bloques:
        texto = " ".join(texto.split())
        if not texto:
            continue
        t_ini = parse_time(t_ini_str).total_seconds() + offset_seg
        t_fin = parse_time(t_fin_str).total_seconds() + offset_seg
        if t_fin > t_ini:
            cues.append((t_ini, t_fin, texto))

    return _agrupar_cues_en_bloques(cues, indice_inicial)


def _partir_cue_largo(t_ini, t_fin, texto):
    """Parte en varios un cue que no entra en pantalla, repartiendo su tiempo
    proporcionalmente a los caracteres de cada parte.

    Hace falta porque edge-tts no siempre corta por palabra: según la versión
    devuelve un cue por FRASE, y una frase entera ocupa el ancho completo del
    cuadro. Agrupar solo sabe unir cues cortos; sin esto, uno largo pasa de
    largo tal cual."""
    palabras = texto.split()
    duracion = t_fin - t_ini
    if len(texto) <= CARACTERES_MAX_SUBTITULO or len(palabras) < 2:
        return [(t_ini, t_fin, texto)]

    # Cuántas partes: las que pida el ancho, salvo que el tiempo no alcance para
    # que cada una se lea. Si no alcanza, se admiten dos líneas por parte antes
    # que partes que parpadeen.
    por_ancho = math.ceil(len(texto) / CARACTERES_MAX_SUBTITULO)
    por_tiempo = max(1, int(duracion // SEGUNDOS_MIN_SUBTITULO))
    minimo = math.ceil(len(texto) / (CARACTERES_MAX_SUBTITULO * 2))
    partes_n = max(minimo, min(por_ancho, por_tiempo))
    if partes_n < 2:
        return [(t_ini, t_fin, texto)]

    objetivo = len(texto) / partes_n
    grupos, actual, ancho = [], [], 0
    for palabra in palabras:
        actual.append(palabra)
        ancho += len(palabra) + 1
        corta_aqui = ancho >= objetivo and len(grupos) < partes_n - 1
        # Si la palabra cierra una frase, cortar acá aunque falte poco para el
        # objetivo: el corte cae en la pausa real.
        if corta_aqui or (palabra.endswith(PUNTUACION_FUERTE + PUNTUACION_DEBIL)
                          and ancho >= objetivo * 0.7 and len(grupos) < partes_n - 1):
            grupos.append(actual)
            actual, ancho = [], 0
    if actual:
        grupos.append(actual)

    textos = [" ".join(g) for g in grupos]
    total = sum(len(t) for t in textos) or 1
    salida, t = [], t_ini
    for i, txt in enumerate(textos):
        dur = duracion * len(txt) / total
        fin = t_fin if i == len(textos) - 1 else t + dur
        salida.append((t, fin, txt))
        t = fin
    return salida


def _agrupar_cues_en_bloques(cues, indice_inicial):
    """Agrupa los cues palabra a palabra de edge-tts en bloques legibles.

    Agrupar de a N palabras fijas (lo que se hacía antes) deja el subtítulo
    1.4s en pantalla y parte las frases donde toque: "QUE HAS ESTADO
    APLAZANDO", "DE HACERLO TODO PERFECTO,". Acá el corte lo deciden la
    puntuación, el ancho de línea y un piso de tiempo, que es el criterio de
    subtitulado de siempre: cortar donde el que habla hace la pausa."""
    # Primero se parte lo que no entra en pantalla; recién después se agrupa lo
    # que quedó corto. edge-tts entrega cues por palabra o por frase entera
    # según la versión, así que hacen falta las dos pasadas.
    partidos = []
    for cue in cues:
        partidos.extend(_partir_cue_largo(*cue))

    grupos = []
    acumulado = []
    for t_ini, t_fin, texto in partidos:
        if acumulado:
            texto_actual = " ".join(t for _, _, t in acumulado)
            duracion = acumulado[-1][1] - acumulado[0][0]
            if _cierra_bloque(texto_actual, texto, duracion):
                grupos.append(acumulado)
                acumulado = []
        acumulado.append((t_ini, t_fin, texto))
    if acumulado:
        grupos.append(acumulado)

    # El último bloque de la escena puede quedar por debajo del mínimo: se cerró
    # porque se acabó el texto, no porque tocara cortar. Se funde con el
    # anterior; acá sí se admiten dos líneas (el conversor a ASS las parte),
    # porque un bloque de dos líneas se lee sin problema y uno de medio segundo
    # en pantalla no.
    if len(grupos) >= 2:
        ultimo, previo = grupos[-1], grupos[-2]
        dur_ultimo = ultimo[-1][1] - ultimo[0][0]
        chars = sum(len(t) + 1 for _, _, t in previo + ultimo)
        if dur_ultimo < SEGUNDOS_MIN_SUBTITULO and chars <= CARACTERES_MAX_SUBTITULO * 2:
            grupos[-2:] = [previo + ultimo]

    lineas = []
    idx = indice_inicial
    for grupo in grupos:
        lineas.append(_bloque_srt(grupo, idx))
        idx += 1
    return lineas, idx


def _bloque_srt(cues, indice):
    t_ini = cues[0][0]
    t_fin = cues[-1][1]
    texto = " ".join(t for _, _, t in cues)
    return f"{indice}\n{formatear_srt_time(t_ini)} --> {formatear_srt_time(t_fin)}\n{texto}\n"


def generar_bloques_srt_escena(texto, duracion_seg, offset_seg, indice_inicial):
    """Fallback para motores de TTS que no devuelven marcas de tiempo por
    palabra (Gemini TTS): reparte la duración medida del audio entre las
    palabras, proporcionalmente a su longitud, y agrupa con el mismo criterio de
    frase que la ruta buena — así el corte de línea es igual de legible aunque
    la sincronía sea estimada."""
    palabras = texto.split()
    if not palabras or duracion_seg <= 0:
        return [], indice_inicial

    total_chars = sum(len(p) for p in palabras) or 1
    cues = []
    t_acum = 0.0
    for palabra in palabras:
        dur = duracion_seg * (len(palabra) / total_chars)
        cues.append((offset_seg + t_acum, offset_seg + t_acum + dur, palabra))
        t_acum += dur

    return _agrupar_cues_en_bloques(cues, indice_inicial)


def parse_time(time_str):
    from datetime import datetime
    pt = datetime.strptime(time_str.replace(',', '.'), "%H:%M:%S.%f")
    return timedelta(hours=pt.hour, minutes=pt.minute, seconds=pt.second, microseconds=pt.microsecond)


def format_ass_time(td):
    ts = int(td.total_seconds())
    return f"{ts // 3600}:{(ts % 3600) // 60:02d}:{ts % 60:02d}.{int(td.microseconds / 10000):02d}"


def _corte_dos_lineas(palabras):
    """Índice donde partir el bloque en dos líneas, o None si entra en una.

    Se parte por la mitad en vez de llenar la primera línea hasta el tope:
    llenando, la última palabra cae sola abajo ("...EL TRABAJO EN / SÍ.") y se
    lee peor que dos líneas parejas."""
    texto = " ".join(palabras)
    if len(texto) <= CARACTERES_MAX_LINEA:
        return None

    mitad = len(texto) / 2
    mejor, mejor_dist = None, None
    ancho = 0
    for j in range(1, len(palabras)):
        ancho += len(palabras[j - 1]) + 1
        dist = abs(ancho - mitad)
        if mejor_dist is None or dist < mejor_dist:
            mejor, mejor_dist = j, dist
    return mejor


def _tiempos_por_palabra(palabras, t_inicio, t_fin):
    """Reparte la duración del bloque entre sus palabras, proporcional a los
    caracteres de cada una: las palabras largas se pronuncian más lento."""
    total = sum(len(p) for p in palabras) or 1
    duracion = (t_fin - t_inicio).total_seconds()
    tiempos, t = [], t_inicio
    for i, palabra in enumerate(palabras):
        fin = t_fin if i == len(palabras) - 1 else t + timedelta(seconds=duracion * len(palabra) / total)
        tiempos.append((t, fin))
        t = fin
    return tiempos


def _color_resaltado(palabra, contador_color):
    """Color de acento para la palabra resaltada, o None si va en blanco.

    Devuelve también el contador actualizado: la paleta rota entre palabras
    coloreadas a lo largo de todo el video, no dentro de cada bloque, que es
    como se comporta en la referencia."""
    if len(palabra.strip(".,;:¿?¡!—…\"'()")) < LARGO_MIN_PALABRA_COLOREADA:
        return None, contador_color
    return PALETA_RESALTADO[contador_color % len(PALETA_RESALTADO)], contador_color + 1


def _linea_resaltada(palabras, indice_resaltada, corte, color=None):
    """La frase completa, con una sola palabra resaltada en color y tamaño.

    Se emite la frase entera en cada línea de diálogo —no solo la palabra de
    turno— para que el espectador lea el contexto y no palabras sueltas. El
    resaltado marca dónde va la voz."""
    partes = []
    for j, palabra in enumerate(palabras):
        if j == corte:
            partes.append("\\N")
        if j == indice_resaltada:
            abre = (
                f"\\fscx{round(ESCALA_BASE_X * FACTOR_RESALTADO)}"
                f"\\fscy{round(100 * FACTOR_RESALTADO)}"
            )
            cierra = f"\\fscx{ESCALA_BASE_X}\\fscy100"
            if color:
                abre += f"\\c{color}"
                cierra += f"\\c{COLOR_BASE}"
            partes.append(f"{{{abre}}}{palabra.upper()}{{{cierra}}} ")
        else:
            partes.append(f"{palabra.upper()} ")
    return "".join(partes).replace(" \\N", "\\N").strip()


def convertir_srt_a_karaoke_ass(srt_in_path, ass_out_path, w, h):
    """Escribe el ASS de los subtítulos: la frase completa en pantalla, con la
    palabra que se está diciendo resaltada en color y tamaño.

    Del pipeline hermano video-scout-pipeline se conservan la tipografía
    (Montserrat Black), el contorno negro grueso, la sombra y el adelanto de
    0.32s respecto de la voz. Lo que cambia es la unidad: allá se muestran dos
    palabras por vez y la frase nunca se ve entera; acá se muestra la frase
    completa y el resaltado indica por dónde va la locución, que es lo que deja
    leer el contexto en vez de palabras sueltas.

    El subtítulo se ancla abajo (Alignment 2), no centrado como allá: acá el
    fondo son diagramas rotulados que dicen algo, y el prompt de HyperFrames ya
    les reserva la franja inferior."""
    es_vertical = h > w
    font_size = 72 if es_vertical else 64
    # En vertical el subtítulo no va abajo del todo: la interfaz de Shorts y de
    # TikTok tapa esa franja con el título, el usuario y los botones. Va cerca de
    # la mitad del cuadro, como en el video de referencia (Alignment 2 con un
    # margen inferior grande, porque libass ignora MarginV con el anclaje al
    # medio). En horizontal sigue abajo, donde el diagrama le deja lugar.
    margen_inferior = int(h * 0.46) if es_vertical else 60
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n"
        f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        f"BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        f"Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Bold=1 e Italic=1, ScaleX condensado: la itálica pesada y angosta de
        # la referencia. Contorno 6 y sombra 2, que es lo que la hace legible
        # sobre cualquier fondo.
        f"Style: Karaoke,Montserrat Black,{font_size},{COLOR_BASE},{COLOR_BASE},&H00000000&,&H80000000&,1,1,0,0,"
        f"{ESCALA_BASE_X},100,0,0,1,6,2,2,60,60,{margen_inferior},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
        f"MarginR, MarginV, Effect, Text\n"
    )

    if not os.path.exists(srt_in_path):
        with open(ass_out_path, 'w', encoding='utf-8') as f:
            f.write(header)
        return

    with open(srt_in_path, 'r', encoding='utf-8') as f:
        contenido = f.read()
    bloques = re.findall(
        r'(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*?)(?=\n\n|\Z)',
        contenido, re.DOTALL,
    )
    lineas_ass = [header]
    contador_color = 0

    for _, t_inicio_str, t_fin_str, texto in bloques:
        palabras = texto.strip().replace('\n', ' ').split()
        if not palabras:
            continue

        # Adelanto de 0.32s, igual que en el pipeline hermano: el subtítulo
        # entra un instante antes que la sílaba, que es como se lee sin sentir
        # que va atrasado. Nunca antes del inicio del video.
        t_inicio = max(timedelta(0), parse_time(t_inicio_str) - ADELANTO_SUBTITULO)
        t_fin = max(timedelta(0), parse_time(t_fin_str) - ADELANTO_SUBTITULO)
        if (t_fin - t_inicio).total_seconds() <= 0:
            continue

        corte = _corte_dos_lineas(palabras)
        for i, (t_ini_p, t_fin_p) in enumerate(_tiempos_por_palabra(palabras, t_inicio, t_fin)):
            if (t_fin_p - t_ini_p).total_seconds() <= 0:
                continue
            color, contador_color = _color_resaltado(palabras[i], contador_color)
            lineas_ass.append(
                f"Dialogue: 0,{format_ass_time(t_ini_p)},{format_ass_time(t_fin_p)},Karaoke,,0,0,0,,"
                f"{_linea_resaltada(palabras, i, corte, color)}\n"
            )

    with open(ass_out_path, 'w', encoding='utf-8') as f:
        f.writelines(lineas_ass)


# ---------------------------------------------------------
# MÚSICA DE FONDO (pistas de actualizar_musica.py)
# ---------------------------------------------------------
def detectar_tono_video(texto):
    dic = {
        'inspirador': ['inspir', 'motiv', 'logro', 'superac', 'exito', 'crecimiento'],
        'tenso': ['miedo', 'ansiedad', 'trauma', 'evitar', 'presion', 'estres'],
        'esperanzador': ['esperanza', 'sanar', 'cambio', 'nuevo comienzo', 'paz'],
    }
    val = texto.lower()
    return next((tono for tono, kws in dic.items() if any(k in val for k in kws)), 'reflexivo')


def seleccionar_musica_fondo(tono):
    exts = ('.m4a', '.mp3', '.wav', '.aac')

    def candidatas(prefijo):
        return [f for f in os.listdir(BASE_DIR) if f.startswith(prefijo) and f.endswith(exts) and archivo_valido(os.path.join(BASE_DIR, f))]

    for prefijo in (f"musica_{tono}", "musica_fondo"):
        opciones = candidatas(prefijo)
        if opciones:
            return os.path.join(BASE_DIR, random.choice(opciones))

    opciones = candidatas("musica_")
    return os.path.join(BASE_DIR, random.choice(opciones)) if opciones else None


# ---------------------------------------------------------
# PARSEO DEL GUION
# ---------------------------------------------------------
def armar_video_escena(clips_base, dur_escena, w, h, ruta_salida, gestor, num_escena):
    """Monta el video de una escena repartiendo su duración entre los planos.

    La narración de la escena sigue de corrido; lo que cambia es la imagen, cada
    `dur_escena / n` segundos. Con un solo plano el resultado es el de siempre:
    ese clip en bucle hasta cubrir la narración."""
    filtro = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS_VIDEO}"

    # El reparto se hace en FOTOGRAMAS, no en segundos. Cortando por tiempo,
    # ffmpeg redondea cada tramo al fotograma más cercano y con varios planos por
    # escena esos redondeos se suman: el video queda unas décimas más corto que
    # su narración, y el desfase se acumula escena a escena hasta que la imagen
    # va notoriamente atrasada respecto de la voz al final del video.
    total_frames = max(1, round(dur_escena * FPS_VIDEO))
    por_plano, sobrante = divmod(total_frames, len(clips_base))
    frames = [por_plano + (1 if j < sobrante else 0) for j in range(len(clips_base))]

    tramos = []
    for j, (clip, n_frames) in enumerate(zip(clips_base, frames), 1):
        if n_frames <= 0:
            continue
        tramo = gestor.registrar(f"escena_{num_escena}_plano_{j}.mp4")
        ejecutar_comando(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-stream_loop", "-1", "-i", clip, "-vf", filtro, "-frames:v", str(n_frames),
             "-an", "-c:v", "libx264", "-preset", "ultrafast", tramo],
            f"FFmpeg: plano {j} de la escena {num_escena}",
        )
        tramos.append(tramo)

    if len(tramos) == 1:
        shutil.copyfile(tramos[0], ruta_salida)
        return

    lista = gestor.registrar(f"escena_{num_escena}_planos.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for tramo in tramos:
            f.write(f"file '{os.path.abspath(tramo)}'\n")
    ejecutar_comando(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", lista, "-c", "copy", ruta_salida],
        f"FFmpeg: unión de los planos de la escena {num_escena}",
    )


def parsear_bloque_guion(bloque):
    dia = int(re.search(r'#\s*Dia:\s*(\d+)', bloque).group(1))
    tema = re.search(r'#\s*Tema:\s*(.+)', bloque).group(1).strip()
    hook = re.search(r'#\s*TituloHook:\s*(.+)', bloque).group(1).strip()
    m_dur = re.search(r'#\s*DuracionObjetivoMin:\s*([\d.]+)', bloque)
    dur_obj = float(m_dur.group(1)) if m_dur else 8.0

    trozos = re.split(r'###\s*ESCENA\s*\n?', bloque)[1:]
    escenas = []
    for trozo in trozos:
        m_visual = re.search(r'VISUAL:\s*(.+?)(?=\nTEXTO:|\Z)', trozo, re.DOTALL)
        m_texto = re.search(r'TEXTO:\s*(.+)', trozo, re.DOTALL)
        if not (m_visual and m_texto):
            continue
        # Una escena puede traer varias líneas VISUAL:, una por plano. Es lo que
        # permite que la imagen cambie cada pocos segundos en vez de quedarse
        # fija los 15-25s que dura la narración. Los guiones viejos traen una
        # sola y siguen funcionando: quedan como una escena de un plano.
        planos = [
            p.strip() for p in re.split(r'\n\s*VISUAL:\s*', m_visual.group(1).strip()) if p.strip()
        ]
        escenas.append({
            "planos": planos,
            "visual": planos[0],
            "texto": m_texto.group(1).strip(),
        })

    return {"dia": dia, "tema": tema, "hook": hook, "duracion_objetivo_min": dur_obj, "escenas": escenas}


# ---------------------------------------------------------
# RENDER DE UN DÍA
# ---------------------------------------------------------
def renderizar_una_historia(bloque, cfg, num=1):
    gestor = GestorTemporales()
    try:
        info = parsear_bloque_guion(bloque)
        if not info["escenas"]:
            raise RuntimeError("El guion no tiene escenas.")

        slug = re.sub(r'[^\w\s-]', '', info["hook"]).strip().replace(' ', '_')[:120] or f"dia_{info['dia']}"
        carpeta_salida = cfg["carpeta_salida"]
        os.makedirs(carpeta_salida, exist_ok=True)
        ruta_out = os.path.join(carpeta_salida, f"{info['dia']:02d}_{slug}.mp4")

        if not cfg["reintentar_existentes"] and archivo_valido(ruta_out):
            logger.info(f"Día {info['dia']} ya existe, se omite: {os.path.basename(ruta_out)}")
            return None

        print(f"\n🎬 [Día {info['dia']}] {info['hook']}  ({len(info['escenas'])} escena(s))")

        w, h = RESOLUCIONES.get(cfg["aspecto_video"], RESOLUCIONES["16:9"])
        configurar_ancho_subtitulos(w, h)
        motor_tts = cfg.get("motor_tts", "gemini")
        es_fem = cfg.get("genero_narrador") == "femenino"
        if motor_tts == "edge":
            voz = cfg.get("voz_femenina_edge" if es_fem else "voz_masculina_edge") or (
                tts_edge.VOZ_FALLBACK_FEMENINA if es_fem else tts_edge.VOZ_FALLBACK_MASCULINA
            )
            ext_audio = "mp3"
        else:
            voz = cfg["voz_femenina"] if es_fem else cfg["voz_masculina"]
            ext_audio = "wav"

        motor = cfg.get("motor_broll", "veo")
        rutas_broll_lote = None
        if motor == "hyperframes":
            # Se piden todos los planos de todas las escenas de una sola vez: el
            # lote es lo que hace viable la cuota de Gemini, y con varios planos
            # por escena hay bastantes más pedidos que antes.
            prompts_visuales = [p for e in info["escenas"] for p in e["planos"]]
            print(f" ├─ 🎞️ Generando {len(prompts_visuales)} plano(s) de apoyo en lotes (hyperframes)...")
            rutas_broll_lote = hyperframes_broll.generar_clips_lote_cacheados(
                prompts_visuales, aspecto=cfg["aspecto_video"], modelo=cfg["modelo_texto"],
                tam_lote=cfg.get("tam_lote_hyperframes", hyperframes_broll.TAM_LOTE_DEFAULT),
            )

        clips_video = []
        rutas_audio = []
        lineas_srt = []
        idx_srt = 1
        t_acum = 0.0
        texto_completo = []
        base_plano = 0  # posición de la escena dentro de rutas_broll_lote, que es plana

        for i, escena in enumerate(info["escenas"], 1):
            print(f" ├─ 🎙️ Escena {i}/{len(info['escenas'])}: locución ({motor_tts})...")
            ruta_audio = gestor.registrar(f"escena_{i}_audio.{ext_audio}")
            ruta_srt_escena = None
            if motor_tts == "edge":
                ruta_srt_escena = gestor.registrar(f"escena_{i}_subtitulos.srt")
                audio_ok = tts_edge.generar_audio(
                    escena["texto"], voz, ruta_audio, ruta_srt_salida=ruta_srt_escena
                )
            else:
                audio_ok = tts_gemini.generar_audio(escena["texto"], voz, ruta_audio, modelo=cfg["modelo_tts"])
            if not audio_ok:
                raise RuntimeError(f"No se pudo generar la locución de la escena {i}.")
            dur_escena = medir_duracion_media(ruta_audio)
            if dur_escena <= 0:
                raise RuntimeError(f"Duración inválida en el audio de la escena {i}.")

            print(f" ├─ 🎞️ Escena {i}/{len(info['escenas'])}: {len(escena['planos'])} plano(s) de apoyo ({motor})...")
            if motor == "manim":
                clips_base = [
                    manim_broll.generar_clip_cacheado(
                        plano, aspecto=cfg["aspecto_video"], modelo=cfg["modelo_texto"]
                    )
                    for plano in escena["planos"]
                ]
            elif motor == "hyperframes":
                clips_base = rutas_broll_lote[base_plano:base_plano + len(escena["planos"])]
            else:
                clips_base = [
                    veo_broll.generar_clip_cacheado(
                        plano, aspecto=cfg["aspecto_video"], modelo=cfg["modelo_veo"]
                    )
                    for plano in escena["planos"]
                ]
            base_plano += len(escena["planos"])

            clips_base = [c for c in clips_base if c]
            if not clips_base:
                raise RuntimeError(f"No se pudo generar el video de apoyo de la escena {i}.")

            ruta_clip_escena = gestor.registrar(f"escena_{i}_video.mp4")
            armar_video_escena(clips_base, dur_escena, w, h, ruta_clip_escena, gestor, i)
            if not archivo_valido(ruta_clip_escena):
                raise RuntimeError(f"El clip ajustado de la escena {i} no es válido.")

            lineas = []
            if ruta_srt_escena:
                lineas, idx_srt = leer_bloques_srt_escena(ruta_srt_escena, t_acum, idx_srt)
            if not lineas:
                lineas, idx_srt = generar_bloques_srt_escena(escena["texto"], dur_escena, t_acum, idx_srt)
            lineas_srt.extend(lineas)

            clips_video.append(ruta_clip_escena)
            rutas_audio.append(ruta_audio)
            texto_completo.append(escena["texto"])
            t_acum += dur_escena

        dur_total = t_acum
        print(f" ├─ 🧩 Ensamblando {len(clips_video)} escena(s) ({dur_total/60:.1f} min)...")

        lista_video = gestor.registrar("lista_video.txt")
        with open(lista_video, "w", encoding="utf-8") as f:
            for c in clips_video:
                f.write(f"file '{os.path.abspath(c)}'\n")
        video_concat = gestor.registrar("video_concatenado.mp4")
        ejecutar_comando(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", lista_video, "-c", "copy", video_concat],
            "FFmpeg: unión de escenas de video",
        )

        lista_audio = gestor.registrar("lista_audio.txt")
        with open(lista_audio, "w", encoding="utf-8") as f:
            for a in rutas_audio:
                f.write(f"file '{os.path.abspath(a)}'\n")
        audio_narracion = gestor.registrar("audio_narracion.m4a")
        ejecutar_comando(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", lista_audio, "-c:a", "aac", "-b:a", "192k", audio_narracion],
            "FFmpeg: unión de audio narrado",
        )

        srt_raw = gestor.registrar("subtitulos.srt")
        with open(srt_raw, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas_srt))
        ass_karaoke = gestor.registrar("subtitulos.ass")
        convertir_srt_a_karaoke_ass(srt_raw, ass_karaoke, w, h)

        img_tarjeta = gestor.registrar("tarjeta_intro.png")
        crear_tarjeta_intro(limpiar_texto_seguro(info["hook"]), img_tarjeta, w, h)

        tono = detectar_tono_video(info["tema"] + " " + " ".join(texto_completo))
        musica = seleccionar_musica_fondo(tono)
        print(f" ├─ 🎼 Tono: {tono.upper()} | Música: {os.path.basename(musica) if musica else 'ninguna'}")

        # Mezcla con ducking nativo de HyperFrames (voiceover carve): recorta
        # solo las bandas de frecuencia que ocupa la voz en vez de bajar el
        # volumen fijo de toda la música (mezcla estática de siempre, que
        # queda como fallback si el carve falla o está desactivado).
        audio_final = audio_narracion
        usar_musica_estatica = bool(musica)
        if musica and cfg.get("ducking_hyperframes", True):
            fade_inicio = max(0.0, dur_total - 2.0)
            musica_looped = gestor.registrar(f"musica_looped{os.path.splitext(musica)[1] or '.mp3'}")
            ejecutar_comando(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-stream_loop", "-1", "-i", musica, "-t", f"{dur_total:.3f}",
                 "-af", f"afade=t=out:st={fade_inicio:.2f}:d=2",
                 "-c:a", "aac", "-b:a", "192k", musica_looped],
                "FFmpeg: preparar música de fondo (loop + fade)",
            )
            audio_mezclado = gestor.registrar("audio_mezclado.m4a")
            print(" ├─ 🎚️ Mezclando narración + música (ducking nativo de HyperFrames)...")
            if hyperframes_audio_mix.mezclar_narracion_musica(
                audio_narracion, musica_looped, dur_total, audio_mezclado,
                fuerza=cfg.get("fuerza_carve_musica", hyperframes_audio_mix.FUERZA_CARVE_DEFAULT),
            ):
                audio_final = audio_mezclado
                usar_musica_estatica = False
            else:
                print(" ├─ ⚠️ Ducking nativo falló, se usa mezcla estática de música.")

        f_ass = ass_karaoke.replace('\\', '\\\\').replace(':', '\\:')
        dur_tarjeta = (
            DURACION_INTRO_CARD_SHORT_SEG if cfg.get("formato") == "short"
            else DURACION_INTRO_CARD_SEG
        )
        # Con la tarjeta en 0 se saca el overlay del filtro entero: dejarlo con
        # enable='between(t,0,0)' haría decodificar la imagen para nada.
        pre_ass = (
            f"[0:v][2:v]overlay=0:0:enable='between(t,0,{dur_tarjeta})'[bgc];[bgc]"
            if dur_tarjeta > 0 else "[0:v]"
        )
        if usar_musica_estatica:
            fade_inicio = max(0.0, dur_total - 2.0)
            fc = (
                f"{pre_ass}ass='{f_ass}'[vout];"
                f"[1:a]volume=1.0[av];[3:a]volume=0.08,afade=t=out:st={fade_inicio:.2f}:d=2[am];"
                f"[av][am]amix=inputs=2:duration=first[aout]"
            )
            cmd_ff = ["ffmpeg", "-hide_banner", "-y", "-i", video_concat, "-i", audio_narracion,
                      "-i", img_tarjeta, "-stream_loop", "-1", "-i", musica,
                      "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"]
        else:
            fc = f"{pre_ass}ass='{f_ass}'[vout]"
            cmd_ff = ["ffmpeg", "-hide_banner", "-y", "-i", video_concat, "-i", audio_final,
                      "-i", img_tarjeta, "-filter_complex", fc, "-map", "[vout]", "-map", "1:a:0"]

        flags_audio = ["-map_metadata", "-1", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        flags_gpu = ["-hwaccel", "cuda", "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "19"]
        flags_cpu = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]

        def ejecutar_render(flags_encoder):
            cmd = cmd_ff + flags_encoder + flags_audio + [ruta_out]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            return res.returncode == 0 and archivo_valido(ruta_out)

        print(" ├─ 🚀 Renderizando...")
        exito = ejecutar_render(flags_gpu)
        if not exito:
            logger.warning(f"Render GPU falló para el día {info['dia']}, reintentando con CPU.")
            exito = ejecutar_render(flags_cpu)
        if not exito:
            raise RuntimeError("El render final falló tanto en GPU como en CPU.")

        print(f"✅ ¡Día {info['dia']} completado!: {os.path.basename(ruta_out)}")
        logger.info(f"Día {info['dia']} completado: {ruta_out} ({dur_total:.1f}s, tono={tono})")
        return {
            "dia": info["dia"],
            "titulo": info["hook"],
            "tema": info["tema"],
            "ruta": ruta_out,
            "tono": tono,
            "cuerpo": " ".join(texto_completo),
            "duracion_sec": round(dur_total, 1),
            "musica_archivo": os.path.basename(musica) if musica else None,
        }
    finally:
        gestor.limpiar()


def renderizar_lote_historias(archivo="guion.txt"):
    cfg = cargar_config()
    print("--------------------------------------------------\n🟢 INICIANDO GENERADOR DE VIDEOS\n--------------------------------------------------")

    comprobar_dependencias()

    if not os.path.exists(archivo):
        print(f"❌ Error: No se encontró '{archivo}'.")
        # Sin el raise, pipeline.py reporta esta etapa como OK aunque no
        # haya nada que renderizar (típicamente porque la etapa de guion
        # falló antes y esta corrida no tiene con qué trabajar).
        raise RuntimeError(f"No se encontró '{archivo}'.")

    with open(archivo, 'r', encoding='utf-8') as f:
        bloques = [h.strip() for h in f.read().split("===NUEVA_HISTORIA===") if h.strip()]

    if not bloques:
        print("❌ No se detectaron días de guion.")
        raise RuntimeError(f"'{archivo}' no tiene días de guion detectables.")

    print(f"📦 Total de días en el guion: {len(bloques)}")
    fallidos = []
    completados = []

    for i, bloque in enumerate(bloques, 1):
        try:
            resultado = renderizar_una_historia(bloque, cfg, i)
            if resultado:
                completados.append(resultado)
        except Exception as exc:
            m = re.search(r'#\s*Dia:\s*(\d+)', bloque)
            dia = int(m.group(1)) if m else i
            fallidos.append((dia, str(exc)))
            logger.error(f"Día {dia} falló: {exc}")
            print(f"\n❌ Día {dia} falló: {exc}")

    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    try:
        with open(RUTA_RESULTADO, "w", encoding="utf-8") as f:
            json.dump({
                "completados": completados,
                "fallidos": [{"dia": d, "error": e} for d, e in fallidos],
            }, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning(f"No se pudo escribir {RUTA_RESULTADO}: {exc}")

    print("--------------------------------------------------")
    if fallidos:
        print(f"⚠️ Lote terminado con {len(fallidos)} día(s) fallido(s).")
        for dia, error in fallidos:
            print(f"   • Día {dia}: {error}")
    else:
        print("🎉 ¡PROCESAMIENTO POR LOTE COMPLETADO SIN ERRORES!")
    print("--------------------------------------------------")

    if fallidos:
        # Sin esto, pipeline.py reporta la etapa como OK aunque 0 videos se
        # hayan generado (renderizar_una_historia ya atrapa sus propios
        # errores por día, así que aquí no hay excepción que se propague sola).
        raise RuntimeError(f"{len(fallidos)} día(s) fallaron al renderizar. Ver detalle arriba.")


if __name__ == "__main__":
    renderizar_lote_historias()
