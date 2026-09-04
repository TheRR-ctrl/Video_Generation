"""
Script Writer — convierte pipeline_state/plan_contenido.json (salida de
content_planner.py) en guion.txt, dividiendo cada video en escenas: cada
escena trae el texto narrado y un prompt visual en inglés para generar su
clip de video de apoyo con IA (Veo, ver veo_broll.py).

Usa Gemini (capa gratuita) para escribir el guion completo de cada día.

Requiere: pip install -U google-genai
Credenciales: GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey).
"""
import os
import re
import time
import json
import logging

import env_local  # noqa: F401 (carga .env si existe)

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

from gemini_utils import llamar_con_reintentos

PAUSA_ENTRE_DIAS_SEG = 5.0  # evita ráfagas de requests que disparen el límite por minuto

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARPETA_ESTADO = os.path.join(BASE_DIR, "pipeline_state")
RUTA_PLAN = os.path.join(CARPETA_ESTADO, "plan_contenido.json")
RUTA_GUION = os.path.join(BASE_DIR, "guion.txt")
RUTA_CONFIG = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULT = {
    "modelo_texto": "gemini-3.6-flash",
}

SCHEMA_GUION = {
    "type": "object",
    "properties": {
        "escenas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "texto": {
                        "type": "string",
                        "description": "Narración de 15-25 segundos (~40-70 palabras en español), tono cercano y natural para locución.",
                    },
                    "prompt_visual": {
                        "type": "string",
                        "description": "Descripción en inglés, concreta y filmable, para generar con IA un clip de video que ilustre esta escena. Sin texto en pantalla, sin rostros reconocibles, sin marcas/logos.",
                    },
                },
                "required": ["texto", "prompt_visual"],
            },
        },
    },
    "required": ["escenas"],
}

SYSTEM_PROMPT = """Eres guionista de un canal de YouTube en español de psicología y
desarrollo personal, formato narrado (voz en off, sin presentador en cámara), con
video de apoyo generado por IA sincronizado escena a escena.

Reglas:
- Divide el guion en escenas de 15-25 segundos de narración cada una (~40-70
  palabras por escena). Genera las escenas necesarias para cubrir la duración
  objetivo a un ritmo de locución de ~140 palabras/minuto en español.
- La primera escena es el hook: una pregunta o afirmación que genere curiosidad o
  tensión inmediata, sin revelar la conclusión.
- Tono cercano, reflexivo, con ejemplos cotidianos. Puedes mencionar conceptos
  psicológicos conocidos, pero NO inventes estudios, cifras ni autores falsos.
- Cierra con una idea aplicable concreta y una invitación breve a comentar o
  suscribirse.
- Nada de lenguaje de texto escrito ("en resumen", "por lo tanto"): debe sonar
  como alguien hablando de viva voz.
- No incluyas markdown ni encabezados en el texto narrado."""


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("script_writer")


def cargar_config():
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def dias_ya_guionados():
    if not os.path.exists(RUTA_GUION):
        return set()
    with open(RUTA_GUION, "r", encoding="utf-8") as f:
        contenido = f.read()
    return {int(n) for n in re.findall(r"^# Dia:\s*(\d+)", contenido, re.MULTILINE)}


def escribir_guion_dia(client, modelo, dia):
    prompt = (
        f"Tema: {dia['tema']}\n"
        f"Título/hook: {dia['titulo_hook']}\n"
        f"Ángulo psicológico: {dia['angulo']}\n"
        f"Palabras clave: {', '.join(dia.get('palabras_clave', []))}\n"
        f"Resumen/arco: {dia['resumen']}\n"
        f"Duración objetivo: {dia['duracion_objetivo_min']} minutos"
    )

    response = llamar_con_reintentos(
        client.models.generate_content,
        model=modelo,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SCHEMA_GUION,
        ),
    )
    return json.loads(response.text)


def construir_bloque_guion(dia, guion):
    partes = [
        f"# Dia: {dia['dia']}",
        f"# Tema: {dia['tema']}",
        f"# TituloHook: {dia['titulo_hook']}",
        f"# DuracionObjetivoMin: {dia['duracion_objetivo_min']}",
    ]
    for escena in guion["escenas"]:
        partes.append("### ESCENA")
        partes.append(f"VISUAL: {escena['prompt_visual'].strip()}")
        partes.append(f"TEXTO: {escena['texto'].strip()}")
    return "\n".join(partes)


def main():
    if not os.path.exists(RUTA_PLAN):
        logger.error(f"No se encontró {RUTA_PLAN}. Corre content_planner.py primero.")
        return

    with open(RUTA_PLAN, "r", encoding="utf-8") as f:
        plan = json.load(f)

    ya_guionados = dias_ya_guionados()
    pendientes = [d for d in plan if d["dia"] not in ya_guionados]
    if not pendientes:
        logger.info("No hay días nuevos del plan que guionar.")
        return

    cfg = cargar_config()
    client = genai.Client()
    bloques = []

    for i, dia in enumerate(pendientes, 1):
        if i > 1:
            time.sleep(PAUSA_ENTRE_DIAS_SEG)
        logger.info(f"[{i}/{len(pendientes)}] Escribiendo guion del día {dia['dia']}: {dia['titulo_hook'][:60]}...")
        try:
            guion = escribir_guion_dia(client, cfg["modelo_texto"], dia)
            if not guion.get("escenas"):
                logger.warning(f"Día {dia['dia']}: el modelo no devolvió escenas, se omite.")
                continue
            bloques.append(construir_bloque_guion(dia, guion))
        except genai_errors.APIError as exc:
            logger.warning(f"Fallo de API en día {dia['dia']}: {exc}")
        except Exception as exc:
            logger.warning(f"Fallo inesperado en día {dia['dia']}: {exc}")

    if not bloques:
        # Sin esto, pipeline.py reporta la etapa como OK aunque 0 días se
        # hayan guionado (los intentos individuales ya atrapan sus propios
        # errores por día, así que aquí no hay excepción que se propague sola).
        raise RuntimeError(f"Ningún día se pudo guionar con éxito ({len(pendientes)} pendiente(s)).")

    contenido_previo = ""
    if os.path.exists(RUTA_GUION):
        with open(RUTA_GUION, "r", encoding="utf-8") as f:
            contenido_previo = f.read().strip()

    separador = "\n\n===NUEVA_HISTORIA===\n"
    nuevo_contenido = separador.join(bloques)
    if contenido_previo:
        nuevo_contenido = contenido_previo + separador + nuevo_contenido

    with open(RUTA_GUION, "w", encoding="utf-8") as f:
        f.write(nuevo_contenido)

    logger.info(f"{len(bloques)} día(s) agregado(s) a {RUTA_GUION}")


if __name__ == "__main__":
    main()
