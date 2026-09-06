"""
Formatos de canal — une en un solo nombre las decisiones que hasta ahora había
que fijar sueltas cada vez que se quería cambiar de estilo de video.

Por qué: en poco tiempo aparecieron varias formas de armar el b-roll, cada una
con su propia combinación de motores y su propio tono:

  - **psicologia**: explicador analítico con diagramas dibujados por código
    (plantillas_broll sobre HyperFrames, sin API).
  - **emocional**: reflexión/poesía sobre fotos de banco reales (fondos_stock
    vía Pexels, con Ken Burns).

Sin una capa que las agrupe, probar una implicaba acordarse a mano de 3-4
parámetros sueltos a la vez —motor_broll, motor_tts, motor_composicion,
hashtags— y así fue como la primera prueba del formato de fotos se disparó
sin querer con el motor de Veo (corrida 34003101637: costó cuota real por
un campo que quedó en su valor por defecto). Y sin carpetas de salida
separadas, dos formatos —o el día de mañana, dos canales— se pisarían el
mismo "Videos Creados/", mezclando lo de uno con lo del otro.

Un "formato_canal" es ese paquete con nombre: motor de b-roll, TTS, carpeta
de salida propia, y hashtags por default. config.json elige uno con
"formato_canal"; "manual" (el default, y lo que corre si no se declara nada)
no toca absolutamente nada — dejando que motor_broll/motor_tts/etc. se
sigan fijando sueltos como hasta ahora, para no romper ningún flujo ya
armado ni ninguna prueba puntual (como forzar un motor distinto al de un
formato, a propósito, para comparar).

Agregar un canal nuevo el día de mañana (otro nicho, otro idioma, otra
cuenta de YouTube) es sumar una entrada acá, no reinventar la mitad del
pipeline ni acordarse de memoria qué combinación de motores hacía falta.
"""
import os
import logging

logger = logging.getLogger("formatos_canal")

FORMATO_MANUAL = "manual"

# La carpeta base bajo la que cada formato tiene su propia subcarpeta, para
# que generar videos de dos formatos distintos (o, más adelante, para dos
# canales) nunca mezcle los archivos de uno con los del otro.
_CARPETA_BASE = "Videos Creados"

FORMATOS = {
    "psicologia": {
        "descripcion": "Explicador analítico con diagramas dibujados por código (plantillas HyperFrames, sin API)",
        "motor_broll": "hyperframes",
        "motor_composicion": "plantillas",
        "motor_tts": "edge",
        "carpeta_salida": os.path.join(_CARPETA_BASE, "psicologia"),
        # Documental, no consumido por código: qué guion semilla usar con
        # este formato al armar el input archivo_guion_semilla del workflow.
        "guion_semilla": "guion.semilla.txt",
        "hashtags_base": ["Psicologia", "DesarrolloPersonal", "SaludMental"],
    },
    "emocional": {
        "descripcion": "Reflexión/poesía sobre fotos de banco reales (Pexels) con Ken Burns",
        "motor_broll": "fotos",
        "motor_tts": "edge",
        "carpeta_salida": os.path.join(_CARPETA_BASE, "emocional"),
        "guion_semilla": "guion.semilla.emocional.txt",
        "hashtags_base": ["Reflexion", "FrasesDeVida", "Emociones"],
    },
}


def aplicar_formato(cfg):
    """Superpone sobre cfg los valores del formato_canal elegido.

    "manual" (default) devuelve cfg sin tocar — es el comportamiento de
    siempre. Con un formato con nombre, sus valores REEMPLAZAN a los que
    haya en cfg para esas claves: el formato es una decisión de "usar este
    paquete completo", no una sugerencia parcial. Para desviarse de un
    preset a propósito (probar otro motor puntualmente, como se hizo para
    validar fondos_stock por primera vez), la forma correcta es
    formato_canal="manual" y fijar los campos sueltos como siempre."""
    nombre = cfg.get("formato_canal", FORMATO_MANUAL)
    if nombre == FORMATO_MANUAL:
        return cfg

    formato = FORMATOS.get(nombre)
    if not formato:
        logger.warning(
            f"formato_canal '{nombre}' desconocido; se sigue en modo manual. "
            f"Opciones: {', '.join(FORMATOS)} o '{FORMATO_MANUAL}'."
        )
        return cfg

    cfg = dict(cfg)
    cfg.update(formato)
    return cfg
