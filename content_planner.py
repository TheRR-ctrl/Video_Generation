"""
Content Planner — genera un plan de contenido de N días para un canal de
YouTube en español de psicología / desarrollo personal narrado (sin
presentador en cámara), inspirado en el tono/ángulo de canales de
referencia ya exitosos en el nicho.

Los títulos de los canales de referencia (leídos vía RSS público, ver
reference_channels.py) se usan SOLO como ejemplo de estilo: el modelo
recibe instrucción explícita de no copiar ni parafrasear ningún título,
sino generar ideas originales con el mismo ángulo psicológico.

Salida: pipeline_state/plan_contenido.json
Cada corrida completa el plan hasta 'dias_plan_contenido' sin repetir los
días ya generados (es incremental: si subes dias_plan_contenido, la
siguiente corrida solo genera los días que faltan).

Requiere: pip install -U google-genai requests
Credenciales: GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey).
"""
import os
import json
import logging

import env_local  # noqa: F401 (carga .env si existe)
import formatos_canal
import formato_video

from google import genai
from google.genai import types as genai_types

import reference_channels
from gemini_utils import llamar_con_reintentos

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_PLAN = os.path.join(CARPETA_ESTADO, "plan_contenido.json")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULT = {
    "canales_referencia": [],
    "videos_por_canal_referencia": 15,
    "dias_plan_contenido": 30,
    "modelo_texto": "gemini-3.6-flash",
    "formato": "largo",
}

# El formato manda sobre casi todo lo que sigue: un short y un video largo no se
# planifican igual. Se lee de config.json ("formato": "short" | "largo").
FORMATO_SHORT = "short"

GUIA_SHORT = """

Este canal publica SHORTS verticales para YouTube Shorts y TikTok, de 25 a 60
segundos. Eso cambia la estrategia:
- El video entero desarrolla UNA sola idea. Nada de "tres estrategias": una, la
  más contraintuitiva, contada hasta el final.
- El hook va en los primeros 2 segundos, antes de cualquier contexto. Si la
  primera frase no genera tensión, el espectador desliza.
- El título es corto (menos de 60 caracteres) y funciona como la frase que se
  dice al abrir, no como el título de un ensayo.
- El cierre remata la idea; no hay espacio para pedir suscripción ni resumir."""

GUIA_LARGO = """

Este canal publica videos largos horizontales de 6 a 15 minutos, con espacio
para desarrollar varias ideas y ejemplos."""

def construir_schema_plan(formato):
    es_short = formato == FORMATO_SHORT
    desc_duracion = (
        "Duración objetivo en minutos. Es un short: entre 0.5 y 1 (o sea 30 a 60 segundos)."
        if es_short else
        "Duración objetivo del video en minutos (entre 6 y 15)."
    )
    desc_titulo = (
        "Título del short, menos de 60 caracteres. Es la frase con la que abre el video, "
        "no el título de un ensayo: tiene que abrir un hueco de curiosidad sobre algo que "
        "el espectador ya vivió. Preferí la pregunta directa en segunda persona "
        "(\"¿Por qué te acordás de lo que dijiste hace diez años?\"); una afirmación solo "
        "si es una paradoja que se contradice sola (\"Intentar dormir es lo que te mantiene "
        "despierto\"). Nunca un tema enunciado (\"La importancia del descanso\")."
        if es_short else
        "Título/hook para YouTube: pregunta abierta sobre algo cotidiano que el espectador "
        "nunca se detuvo a preguntarse (\"¿Qué hacían nuestros antepasados todo el día?\"). "
        "Genera curiosidad, sin clickbait engañoso."
    )
    desc_resumen = (
        "2-3 frases con el arco del short: el hook que abre, la idea que lo explica y el "
        "remate. Una sola idea de punta a punta."
        if es_short else
        "2-4 frases con el arco del video: apertura, desarrollo, cierre. Sirve de base para el guionista."
    )
    return {
        "type": "object",
        "properties": {
            "dias": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tema": {"type": "string", "description": "Tema central del video, 1 frase."},
                        "titulo_hook": {"type": "string", "description": desc_titulo},
                        "angulo": {"type": "string", "description": "Ángulo o tesis psicológica que desarrolla el video."},
                        "palabras_clave": {"type": "array", "items": {"type": "string"}},
                        "resumen": {"type": "string", "description": desc_resumen},
                        "duracion_objetivo_min": {"type": "number", "description": desc_duracion},
                    },
                    "required": ["tema", "titulo_hook", "angulo", "palabras_clave", "resumen", "duracion_objetivo_min"],
                },
            },
        },
        "required": ["dias"],
    }

SYSTEM_PROMPT = """Eres estratega de contenido para un canal de YouTube en español de
psicología, desarrollo personal y motivación. El canal es "sin rostro": no hay
presentador en cámara, solo narración en off sobre video de apoyo generado por IA.

Se te dan títulos y descripciones REALES de canales similares y exitosos del mismo
nicho, únicamente como referencia de tono y ángulo. Reglas estrictas:
- NO copies ni parafrasees directamente ningún título ni idea de la referencia.
- Genera temas ORIGINALES con el mismo tipo de ángulo psicológico: una tensión o
  creencia común, una explicación con base psicológica, y una idea aplicable.
- Cada día del plan debe cubrir un ángulo distinto (no repitas el mismo insight
  con otras palabras).
- Los títulos deben generar curiosidad genuina, sin prometer algo que el video no
  vaya a cumplir (nada de clickbait engañoso).
- La forma que mejor funciona en este nicho es la PREGUNTA ABIERTA sobre algo
  cotidiano que el espectador vivió mil veces y nunca se detuvo a preguntarse.
  El canal de referencia llegó a 160.000 suscriptores con 21 videos usando
  exactamente eso: "¿Qué hacían nuestros antepasados todo el día?", "¿Qué
  soñaban los primeros humanos?". Funciona porque el espectador no puede
  contestarla solo, y esa es la razón por la que se queda.
  Lo que NO funciona es el tema enunciado ("La psicología del descanso", "Cómo
  mejorar tu concentración"): no abre ningún hueco, informa que el video existe.
  La única afirmación que compite con una pregunta es la paradoja que se
  contradice sola ("Intentar dormir es lo que te mantiene despierto"), porque el
  hueco lo abre igual. Si tu título no es ninguna de las dos, reescribilo.
- Evita cualquier consejo que se preste a diagnóstico clínico; es contenido de
  divulgación/autoayuda, no terapia."""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("content_planner")


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return formatos_canal.aplicar_formato(cfg)


def cargar_plan():
    if os.path.exists(RUTA_PLAN):
        with open(RUTA_PLAN, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def guardar_plan(plan):
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    with open(RUTA_PLAN, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def construir_bloque_referencia(referencias):
    if not referencias:
        return "(No se pudieron leer canales de referencia; usa tu propio criterio del nicho.)"
    bloques = []
    for ref in referencias:
        titulos = "\n".join(f"  - {v['titulo']}" for v in ref["videos"])
        bloques.append(f"Canal de referencia \"{ref['nombre_canal']}\":\n{titulos}")
    return "\n\n".join(bloques)


def generar_dias_faltantes(client, cfg, referencias, dias_existentes, cantidad_a_generar):
    formato = formato_video.formato_de(cfg)
    temas_existentes = "\n".join(f"- {d['tema']}" for d in dias_existentes) or "(ninguno todavía)"
    prompt = (
        f"{construir_bloque_referencia(referencias)}\n\n"
        f"Temas ya usados en este plan (no los repitas ni los parafrasees):\n{temas_existentes}\n\n"
        f"Genera {cantidad_a_generar} día(s) nuevo(s) de plan de contenido, distintos entre sí "
        f"y de los temas ya usados."
    )

    response = llamar_con_reintentos(
        client.models.generate_content,
        model=cfg["modelo_texto"],
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT + (
                GUIA_SHORT if formato == FORMATO_SHORT else GUIA_LARGO
            ),
            response_mime_type="application/json",
            response_schema=construir_schema_plan(formato),
        ),
    )
    data = json.loads(response.text)
    return data.get("dias", [])


def main():
    cfg = cargar_config()
    plan = cargar_plan()

    objetivo = cfg["dias_plan_contenido"]
    if len(plan) >= objetivo:
        logger.info(f"El plan ya tiene {len(plan)} día(s) (objetivo: {objetivo}). Nada que hacer.")
        return

    faltan = objetivo - len(plan)
    logger.info(f"Plan actual: {len(plan)} día(s). Generando {faltan} día(s) más...")

    referencias = reference_channels.recolectar_referencias(
        cfg["canales_referencia"], cfg["videos_por_canal_referencia"]
    )
    for ref in referencias:
        logger.info(f"Referencia leída: \"{ref['nombre_canal']}\" ({len(ref['videos'])} video(s))")

    client = genai.Client()
    nuevos = generar_dias_faltantes(client, cfg, referencias, plan, faltan)
    if not nuevos:
        # Sin el raise, pipeline.py reporta esta etapa como OK aunque no se
        # haya generado ningún día nuevo de plan.
        raise RuntimeError("El modelo no devolvió días nuevos.")

    siguiente_numero = len(plan) + 1
    for i, dia in enumerate(nuevos):
        dia["dia"] = siguiente_numero + i
        plan.append(dia)

    guardar_plan(plan)
    logger.info(f"{len(nuevos)} día(s) agregado(s). Plan total: {len(plan)}/{objetivo}.")
    for dia in nuevos:
        print(f" • [Día {dia['dia']}] {dia['titulo_hook']}")


if __name__ == "__main__":
    main()
