"""
Script Writer — convierte pipeline_state/plan_contenido.json (salida de
content_planner.py) en guion.txt, dividiendo cada video en escenas: cada
escena trae el texto narrado y un prompt visual para generar su clip de video
de apoyo. El estilo del prompt visual depende de 'motor_broll' en config.json:
descripción filmable en inglés para Veo, o concepto a visualizar en español
para los motores que dibujan con código (hyperframes/manim).

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
    "motor_broll": "veo",
    "formato": "largo",
}

FORMATO_SHORT = "short"

# Un short de 30-60s no se guiona como un video de 10 minutos: la escena de
# 15-25s que sirve en formato largo se come medio short entero.
GUIA_SHORT = """

Esto es un SHORT vertical para YouTube Shorts y TikTok, de 25 a 60 segundos en
total. Reglas que reemplazan a las de duración de arriba:
- Escenas de 5 a 8 segundos de narración (15-25 palabras). En total, entre 5 y 8
  escenas: no más, el short no da para más.
- La primera escena es el hook y arranca EN LA PRIMERA PALABRA. Nada de "hoy
  vamos a hablar de", nada de presentar el tema: la primera frase ya tiene que
  generar la tensión. Quien mira decide en dos segundos si sigue o desliza.
- Una sola idea de punta a punta. Nada de "tres estrategias": una, la más
  contraintuitiva, desarrollada hasta el final.
- El cierre remata la idea y ahí termina. No pidas suscripción ni comentarios, no
  resumas lo dicho: en un short eso es tiempo tirado."""

# El prompt visual tiene que hablarle al motor que realmente va a dibujarlo.
# Veo filma: entiende "cinematic close-up, morning light, 8k". HyperFrames y
# Manim dibujan con código: no pueden filmar a una persona en un escritorio, y
# si se les pide eso terminan inventando decoración abstracta con texto suelto
# que no tiene relación con la narración (ver README, sección motor_broll).
DESC_VISUAL_FILMABLE = (
    "Descripción en inglés, concreta y filmable, para generar con IA un clip de video "
    "que ilustre esta escena. Sin texto en pantalla, sin rostros reconocibles, sin marcas/logos."
)
DESC_VISUAL_MOTION = (
    "Descripción EN ESPAÑOL del DIAGRAMA a dibujar (no de una toma filmada, no de una "
    "metáfora poética). Decí qué elementos concretos aparecen y qué relación muestran "
    "entre ellos: 'dos barras enfrentadas, la de la izquierda tres veces más alta', "
    "'una cadena de tres pasos donde el tercero vuelve al primero'. El espectador tiene "
    "que entender la idea de la escena viendo SOLO ese dibujo, con el audio apagado."
)

# El diagrama tiene que ser de un tipo concreto: cuando el prompt visual queda
# libre, el modelo devuelve metáforas ("una forma que se fragmenta") y el motor
# las dibuja como cuadrados y líneas sueltas que no comunican nada. Pedir el
# arquetipo explícito es lo que convierte la escena en una gráfica útil.
TIPOS_VISUAL = ["comparacion", "proporcion", "evolucion", "proceso", "estructura", "metafora"]

DESC_TIPO_VISUAL = (
    "Arquetipo del diagrama de esta escena: "
    "'comparacion' (dos o más magnitudes enfrentadas: barras), "
    "'proporcion' (una parte contra el total: dona o barra de progreso), "
    "'evolucion' (cómo cambia algo a lo largo del tiempo: línea), "
    "'proceso' (pasos encadenados, causa y efecto, un ciclo que se repite), "
    "'estructura' (las partes de algo y cómo se relacionan entre sí), "
    "'metafora' (SOLO si de verdad no hay nada que comparar, medir, secuenciar ni "
    "descomponer). Preferí siempre uno de los cinco primeros: 'metafora' como máximo "
    "en 1 de cada 5 escenas."
)

DESC_ETIQUETAS_VISUAL = (
    "Las palabras EN ESPAÑOL que van rotuladas sobre el dibujo, una por cada elemento "
    "que representa algo (2 a 4 en total, de 1-3 palabras cada una, sacadas del texto "
    "narrado de esta misma escena). Son las que hacen que el gráfico se entienda: "
    "'Alivio inmediato', 'Meta futura'. No pongas títulos decorativos."
)

DESC_DATOS_VISUAL = (
    "Los números exactos que corresponden, en el mismo orden que las etiquetas, "
    "separados por coma (por ejemplo '80,20'). Si la narración menciona una cifra real, "
    "usala tal cual y NO inventes otras. Si la escena no tiene cifras, dejá el string vacío: "
    "es preferible un diagrama sin números a un dato inventado."
)

# Una escena narra 15-25 segundos. Con un solo dibujo, la imagen se queda quieta
# todo ese rato y el video se siente lento; los canales que funcionan en este
# nicho cambian de imagen cada 3-5 segundos. Por eso cada escena se divide en
# planos: varios dibujos que se turnan mientras la locución sigue de corrido.
PLANOS_POR_ESCENA = (3, 5)
# En un short la escena dura 5-8s: con 3-5 planos cada uno duraría menos de dos
# segundos, que no alcanza ni para armar el diagrama. Dos por escena da ~3s cada
# uno, que es el ritmo de corte del formato.
PLANOS_POR_ESCENA_SHORT = (2, 3)

DESC_PLANOS_VISUAL = (
    "Los PLANOS de la escena: entre {min} y {max} dibujos que se van turnando en pantalla "
    "mientras la narración avanza, en el orden en que aparecen. No son versiones "
    "del mismo dibujo ni escenas distintas: son los momentos de UNA misma idea, "
    "y juntos tienen que contar el arco de lo que se está narrando. "
    "Por ejemplo, si la narración dice que el alivio inmediato gana sobre la meta "
    "futura: (1) una balanza equilibrada con los dos platos rotulados, (2) el plato del "
    "alivio bajando de golpe, (3) el de la meta futura quedando arriba y apagándose. "
    "Cada plano se describe igual que un diagrama suelto —qué elementos hay y qué "
    "relación muestran— y comparte las etiquetas y los datos de la escena. El primero "
    "presenta, los del medio desarrollan y el último deja la conclusión a la vista."
)

MOTORES_MOTION_GRAPHICS = ("hyperframes", "manim")


def construir_schema_guion(motor_broll, formato="largo"):
    es_motion = motor_broll in MOTORES_MOTION_GRAPHICS
    es_short = formato == FORMATO_SHORT
    descripcion_visual = DESC_VISUAL_MOTION if es_motion else DESC_VISUAL_FILMABLE
    propiedades = {
        "texto": {
            "type": "string",
            "description": (
                "Narración de 5-8 segundos (~15-25 palabras en español), tono cercano y "
                "natural para locución." if es_short else
                "Narración de 15-25 segundos (~40-70 palabras en español), tono cercano y "
                "natural para locución."
            ),
        },
        "prompt_visual": {
            "type": "string",
            "description": descripcion_visual,
        },
    }
    requeridos = ["texto", "prompt_visual"]

    if es_motion:
        min_planos, max_planos = PLANOS_POR_ESCENA_SHORT if es_short else PLANOS_POR_ESCENA
        propiedades["planos_visuales"] = {
            "type": "array",
            "items": {"type": "string"},
            "minItems": min_planos,
            "maxItems": max_planos,
            "description": DESC_PLANOS_VISUAL.format(min=min_planos, max=max_planos),
        }
        requeridos.append("planos_visuales")
        propiedades["tipo_visual"] = {
            "type": "string",
            "enum": TIPOS_VISUAL,
            "description": DESC_TIPO_VISUAL,
        }
        propiedades["etiquetas_visual"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": DESC_ETIQUETAS_VISUAL,
        }
        propiedades["datos_visual"] = {
            "type": "string",
            "description": DESC_DATOS_VISUAL,
        }
        requeridos += ["tipo_visual", "etiquetas_visual", "datos_visual"]

    return {
        "type": "object",
        "properties": {
            "escenas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": propiedades,
                    "required": requeridos,
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
- Español neutro latinoamericano, tratando al espectador de TÚ: "cierras",
  "tienes", "puedes". Nada de voseo ("cerrás", "tenés", "podés") ni de "vosotros":
  la locución usa voces mexicanas y el voseo suena de otro país. Ojo, que estas
  instrucciones estén escritas en otro registro no cambia el del guion.
- No incluyas markdown ni encabezados en el texto narrado."""

GUIA_VISUAL_MOTION = """

El video de apoyo NO se filma: se dibuja con código (motion graphics sobre fondo
oscuro, estilo explicador de divulgación). Cada escena es un DIAGRAMA que explica
lo que se está narrando, no una decoración abstracta que acompaña:
- Nada de "cinematic", "close-up", "8k", "golden hour", "depth of field",
  personas, manos, oficinas ni paisajes: nada de eso se puede dibujar así.
- El criterio para juzgar un visual es uno solo: si alguien mira el dibujo con el
  audio apagado, ¿entiende la idea de la escena? Un cuadrado que pulsa o una línea
  que cruza una elipse NO pasan esa prueba; dos barras rotuladas comparándose, un
  ciclo de tres pasos que vuelve al inicio, o una dona con una porción resaltada, sí.
- Por eso cada escena declara además tipo_visual (el arquetipo del diagrama),
  etiquetas_visual (los rótulos en español que van sobre el dibujo) y datos_visual
  (los números exactos, si la narración menciona alguno).
- Al escribir la narración, buscá activamente que haya algo comparable, medible o
  secuenciable en cada escena — una proporción, dos alternativas enfrentadas, tres
  pasos encadenados. Eso es lo que hace que el video se pueda dibujar. Sin inventar
  estudios ni cifras falsas: si no hay un dato real, comparás cualidades, no números.
- Cada escena se dibuja en varios PLANOS que se turnan mientras la locución sigue
  de corrido, uno cada 4-6 segundos. No son escenas distintas ni el mismo dibujo
  repetido: son los momentos de una misma idea. Pensalos como el arco de la
  escena — el primero planta la situación, los del medio la desarrollan, el último
  deja a la vista la conclusión de la que habla la narración en ese tramo."""


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


def escribir_guion_dia(client, modelo, dia, motor_broll="veo", formato="largo"):
    prompt = (
        f"Tema: {dia['tema']}\n"
        f"Título/hook: {dia['titulo_hook']}\n"
        f"Ángulo psicológico: {dia['angulo']}\n"
        f"Palabras clave: {', '.join(dia.get('palabras_clave', []))}\n"
        f"Resumen/arco: {dia['resumen']}\n"
        f"Duración objetivo: {dia['duracion_objetivo_min']} minutos"
    )

    instrucciones = SYSTEM_PROMPT
    if formato == FORMATO_SHORT:
        instrucciones += GUIA_SHORT
    if motor_broll in MOTORES_MOTION_GRAPHICS:
        instrucciones += GUIA_VISUAL_MOTION

    response = llamar_con_reintentos(
        client.models.generate_content,
        model=modelo,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=instrucciones,
            response_mime_type="application/json",
            response_schema=construir_schema_guion(motor_broll, formato),
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
        for prompt_plano in construir_prompts_visuales(escena):
            partes.append(f"VISUAL: {prompt_plano}")
        partes.append(f"TEXTO: {escena['texto'].strip()}")
    return "\n".join(partes)


def construir_prompts_visuales(escena):
    """Una línea VISUAL: por plano de la escena, listas para guion.txt.

    Cada plano lleva el arquetipo, las etiquetas y los datos de la escena
    repetidos, porque el motor de b-roll recibe cada línea suelta y por separado:
    sin eso, el segundo plano en adelante se dibujaría sin saber qué comparar ni
    con qué rótulos. Una escena sin planos (motor Veo, o un guion viejo) devuelve
    una sola línea, que es como funcionaba antes."""
    contexto = []

    tipo = (escena.get("tipo_visual") or "").strip()
    if tipo:
        contexto.append(f"[{tipo}]")

    sufijo = []
    etiquetas = [e.strip() for e in escena.get("etiquetas_visual") or [] if e and e.strip()]
    if etiquetas:
        sufijo.append(f"Etiquetas: {', '.join(etiquetas)}.")

    datos = (escena.get("datos_visual") or "").strip()
    if datos:
        sufijo.append(f"Datos: {datos}.")

    planos = [p.strip() for p in escena.get("planos_visuales") or [] if p and p.strip()]
    if not planos:
        planos = [escena["prompt_visual"].strip()]

    lineas = []
    for i, plano in enumerate(planos, 1):
        partes = list(contexto)
        if len(planos) > 1:
            partes.append(f"(plano {i} de {len(planos)})")
        partes.append(plano)
        partes.extend(sufijo)
        lineas.append(" ".join(partes))
    return lineas


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
            guion = escribir_guion_dia(
                client, cfg["modelo_texto"], dia,
                cfg.get("motor_broll", "veo"), cfg.get("formato", "largo"),
            )
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
