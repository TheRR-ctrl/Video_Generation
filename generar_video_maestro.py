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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("video_maestro")


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
    segundos = max(0.0, segundos)
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    ms = int(round((segundos - int(segundos)) * 1000))
    return f"{horas:02d}:{minutos:02d}:{segs:02d},{ms:03d}"


def generar_bloques_srt_escena(texto, duracion_seg, offset_seg, indice_inicial, palabras_por_grupo=5):
    """No hay marcas de tiempo por palabra (Gemini TTS no las da, a
    diferencia de edge-tts), así que se distribuye el tiempo de cada grupo
    de palabras proporcionalmente a su longitud dentro de la duración
    medida del audio de la escena."""
    palabras = texto.split()
    if not palabras or duracion_seg <= 0:
        return [], indice_inicial

    grupos = [palabras[i:i + palabras_por_grupo] for i in range(0, len(palabras), palabras_por_grupo)]
    textos_grupo = [" ".join(g) for g in grupos]
    total_chars = sum(len(t) for t in textos_grupo) or 1

    lineas = []
    t_acum = 0.0
    idx = indice_inicial
    for texto_g in textos_grupo:
        dur_g = duracion_seg * (len(texto_g) / total_chars)
        t_ini = offset_seg + t_acum
        t_fin = offset_seg + t_acum + dur_g
        lineas.append(f"{idx}\n{formatear_srt_time(t_ini)} --> {formatear_srt_time(t_fin)}\n{texto_g}\n")
        t_acum += dur_g
        idx += 1
    return lineas, idx


def parse_time(time_str):
    from datetime import datetime
    pt = datetime.strptime(time_str.replace(',', '.'), "%H:%M:%S.%f")
    return timedelta(hours=pt.hour, minutes=pt.minute, seconds=pt.second, microseconds=pt.microsecond)


def format_ass_time(td):
    ts = int(td.total_seconds())
    return f"{ts // 3600}:{(ts % 3600) // 60:02d}:{ts % 60:02d}.{int(td.microseconds / 10000):02d}"


def convertir_srt_a_karaoke_ass(srt_in_path, ass_out_path, w, h):
    font_size, palabras_por_grupo = (58, 2)
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n"
        f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        f"BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        f"Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,Montserrat Black,{font_size},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H80000000&,1,0,0,0,"
        f"100,100,0,0,1,6,2,2,80,80,80,0\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
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

    for _, t_inicio_str, t_fin_str, texto in bloques:
        palabras = texto.strip().replace('\n', ' ').split()
        if not palabras:
            continue

        t_inicio = parse_time(t_inicio_str)
        t_fin = parse_time(t_fin_str)
        dur_cs = int((t_fin - t_inicio).total_seconds() * 100)
        if dur_cs <= 0:
            continue

        grupos = [palabras[i:i + palabras_por_grupo] for i in range(0, len(palabras), palabras_por_grupo)]
        dur_grp = dur_cs // max(1, len(grupos))

        t_act = t_inicio
        for grupo in grupos:
            t_sig = t_act + timedelta(seconds=dur_grp / 100.0)
            texto_karaoke = "".join([f"{{\\k{max(6, dur_grp // len(grupo))}}}{p.upper()} " for p in grupo])
            lineas_ass.append(
                f"Dialogue: 0,{format_ass_time(t_act)},{format_ass_time(t_sig)},Karaoke,,0,0,0,,{texto_karaoke.strip()}\n"
            )
            t_act = t_sig

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
        escenas.append({
            "visual": m_visual.group(1).strip(),
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
            prompts_visuales = [e["visual"] for e in info["escenas"]]
            print(f" ├─ 🎞️ Generando {len(prompts_visuales)} video(s) de apoyo en lotes (hyperframes)...")
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

        for i, escena in enumerate(info["escenas"], 1):
            print(f" ├─ 🎙️ Escena {i}/{len(info['escenas'])}: locución ({motor_tts})...")
            ruta_audio = gestor.registrar(f"escena_{i}_audio.{ext_audio}")
            if motor_tts == "edge":
                audio_ok = tts_edge.generar_audio(escena["texto"], voz, ruta_audio)
            else:
                audio_ok = tts_gemini.generar_audio(escena["texto"], voz, ruta_audio, modelo=cfg["modelo_tts"])
            if not audio_ok:
                raise RuntimeError(f"No se pudo generar la locución de la escena {i}.")
            dur_escena = medir_duracion_media(ruta_audio)
            if dur_escena <= 0:
                raise RuntimeError(f"Duración inválida en el audio de la escena {i}.")

            print(f" ├─ 🎞️ Escena {i}/{len(info['escenas'])}: video de apoyo ({motor})...")
            if motor == "manim":
                ruta_clip_base = manim_broll.generar_clip_cacheado(
                    escena["visual"], aspecto=cfg["aspecto_video"], modelo=cfg["modelo_texto"]
                )
            elif motor == "hyperframes":
                ruta_clip_base = rutas_broll_lote[i - 1]
            else:
                ruta_clip_base = veo_broll.generar_clip_cacheado(
                    escena["visual"], aspecto=cfg["aspecto_video"], modelo=cfg["modelo_veo"]
                )
            if not ruta_clip_base:
                raise RuntimeError(f"No se pudo generar el video de apoyo de la escena {i}.")

            ruta_clip_escena = gestor.registrar(f"escena_{i}_video.mp4")
            filtro = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30"
            ejecutar_comando(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-stream_loop", "-1", "-i", ruta_clip_base, "-t", f"{dur_escena:.2f}",
                 "-vf", filtro, "-an", "-c:v", "libx264", "-preset", "ultrafast", ruta_clip_escena],
                f"FFmpeg: ajuste de duración de escena {i}",
            )
            if not archivo_valido(ruta_clip_escena):
                raise RuntimeError(f"El clip ajustado de la escena {i} no es válido.")

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
        if usar_musica_estatica:
            fade_inicio = max(0.0, dur_total - 2.0)
            fc = (
                f"[0:v][2:v]overlay=0:0:enable='between(t,0,{DURACION_INTRO_CARD_SEG})'[bgc];"
                f"[bgc]ass='{f_ass}'[vout];"
                f"[1:a]volume=1.0[av];[3:a]volume=0.08,afade=t=out:st={fade_inicio:.2f}:d=2[am];"
                f"[av][am]amix=inputs=2:duration=first[aout]"
            )
            cmd_ff = ["ffmpeg", "-hide_banner", "-y", "-i", video_concat, "-i", audio_narracion,
                      "-i", img_tarjeta, "-stream_loop", "-1", "-i", musica,
                      "-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"]
        else:
            fc = (
                f"[0:v][2:v]overlay=0:0:enable='between(t,0,{DURACION_INTRO_CARD_SEG})'[bgc];"
                f"[bgc]ass='{f_ass}'[vout]"
            )
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
