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
}

SCHEMA_PLAN = {
    "type": "object",
    "properties": {
        "dias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tema": {"type": "string", "description": "Tema central del video, 1 frase."},
                    "titulo_hook": {"type": "string", "description": "Título/hook para YouTube, genera curiosidad, sin clickbait engañoso."},
                    "angulo": {"type": "string", "description": "Ángulo o tesis psicológica que desarrolla el video."},
                    "palabras_clave": {"type": "array", "items": {"type": "string"}},
                    "resumen": {"type": "string", "description": "2-4 frases con el arco del video: apertura, desarrollo, cierre. Sirve de base para el guionista."},
                    "duracion_objetivo_min": {"type": "number", "description": "Duración objetivo del video en minutos (entre 6 y 15)."},
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
- Evita cualquier consejo que se preste a diagnóstico clínico; es contenido de
  divulgación/autoayuda, no terapia."""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("content_planner")


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


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
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SCHEMA_PLAN,
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
        logger.error("El modelo no devolvió días nuevos.")
        return

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
