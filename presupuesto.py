"""
Presupuesto — tope duro de consumo de la API, contado en disco y por día.

Por qué existe: el 5 de septiembre una sola corrida mal disparada generó diez
clips de Veo y se llevó puesto el saldo prepago de la cuenta. Nada en el
pipeline sabía cuánto llevaba gastado ni tenía forma de negarse: cada módulo
llamaba a la API cuando le tocaba, y el único freno real era que Google
devolviera 429 — o sea, que ya no quedara dinero. Estamos en etapa de pruebas,
donde una corrida se relanza diez veces en una tarde, así que el freno tiene que
estar de este lado.

Cómo funciona: los tres únicos lugares del pipeline que gastan pasan por acá
antes de llamar a la API.

    texto  -> gemini_utils.llamar_con_reintentos (embudo de TODAS las llamadas
              de texto: plan, guion, HyperFrames, Manim, revisión)
    tts    -> tts_gemini.generar_audio
    video  -> veo_broll.generar_clip_cacheado

Si el consumo del día ya llegó al tope, `consumir()` levanta PresupuestoAgotado
y la llamada no se hace. La etapa falla con un mensaje claro en vez de gastar.

Los contadores viven en pipeline_state/gasto_<fecha>.json, no en memoria, por
dos motivos: el pipeline puede correr sus etapas en procesos separados
(`--desde`, `--hasta`), y sobre todo porque en pruebas se relanza el workflow
varias veces seguidas. Un contador en memoria se reiniciaría en cada corrida y
no frenaría nada; el archivo vive en pipeline_state, que el workflow restaura
entre corridas, así que el tope es de verdad por día y no por proceso.

MODO PRUEBAS (`modo_pruebas: true` en config.json, y viene activado): fuerza el
tope de video a CERO pase lo que pase. Es el estado normal de este proyecto
hasta que el programa esté afinado; para gastar hay que apagarlo a propósito Y
poner PERMITIR_VEO=1, dos cosas distintas y ninguna por descuido.
"""
import os
import json
import logging
from datetime import date

logger = logging.getLogger("presupuesto")

CARPETA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_state")

TIPOS = ("texto", "tts", "video")

# Topes por día, pensados para etapa de pruebas: holgados para que una corrida
# legítima nunca los toque, y ajustados para que una corrida en bucle o mal
# disparada choque enseguida. Un día de un video usa ~10-20 llamadas de texto y
# ~5 de TTS; con estos números entran varias corridas completas por día.
TOPES_DEFAULT = {
    "texto": 150,
    "tts": 60,
    # Cero a propósito: Veo es lo único caro y en pruebas no se usa. Subirlo
    # exige apagar modo_pruebas Y poner PERMITIR_VEO=1 (ver veo_broll.py).
    "video": 0,
}

CLAVE_CONFIG = {
    "texto": "max_llamadas_texto_por_dia",
    "tts": "max_llamadas_tts_por_dia",
    "video": "max_clips_veo_por_dia",
}

_topes = dict(TOPES_DEFAULT)
_modo_pruebas = True


class PresupuestoAgotado(RuntimeError):
    """Se alcanzó el tope diario para ese tipo de llamada."""


def configurar(cfg):
    """Lee los topes de config.json. Se llama una vez al arrancar; si no se
    llama, rigen los valores por defecto, que son los más restrictivos."""
    global _topes, _modo_pruebas
    cfg = cfg or {}
    _modo_pruebas = bool(cfg.get("modo_pruebas", True))
    _topes = {}
    for tipo, clave in CLAVE_CONFIG.items():
        valor = cfg.get(clave, TOPES_DEFAULT[tipo])
        _topes[tipo] = int(valor) if isinstance(valor, (int, float)) and valor >= 0 else TOPES_DEFAULT[tipo]
    if _modo_pruebas and _topes["video"] != 0:
        logger.warning(
            f"modo_pruebas está activo: el tope de video baja de {_topes['video']} a 0. "
            f"Para generar video de pago hay que poner modo_pruebas en false."
        )
        _topes["video"] = 0
    return dict(_topes)


def _ruta_libro(dia=None):
    os.makedirs(CARPETA_ESTADO, exist_ok=True)
    return os.path.join(CARPETA_ESTADO, f"gasto_{dia or date.today().isoformat()}.json")


def _leer(dia=None):
    try:
        with open(_ruta_libro(dia), encoding="utf-8") as f:
            datos = json.load(f)
    except Exception:
        datos = {}
    return {t: int(datos.get(t, 0)) for t in TIPOS}


def _escribir(consumo, dia=None):
    try:
        with open(_ruta_libro(dia), "w", encoding="utf-8") as f:
            json.dump(consumo, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        # No poder anotar el gasto no debe tumbar la corrida, pero sí avisar
        # fuerte: sin el archivo el tope deja de ser acumulativo.
        logger.error(f"No se pudo escribir el libro de gasto ({exc}): el tope diario queda sin efecto.")


def consumir(tipo, cantidad=1, detalle=""):
    """Anota `cantidad` llamadas de `tipo` y devuelve el consumo del día.

    Levanta PresupuestoAgotado ANTES de gastar si la llamada pasaría el tope.
    Se llama justo antes de la llamada a la API, nunca después: la idea es no
    hacerla, no enterarse de que se hizo."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de gasto desconocido: {tipo}")

    consumo = _leer()
    tope = _topes.get(tipo, TOPES_DEFAULT[tipo])
    if consumo[tipo] + cantidad > tope:
        motivo = " (modo_pruebas activo)" if _modo_pruebas and tipo == "video" else ""
        raise PresupuestoAgotado(
            f"Tope diario de '{tipo}' alcanzado{motivo}: {consumo[tipo]}/{tope} usadas hoy, "
            f"se pedían {cantidad} más{f' para {detalle}' if detalle else ''}. "
            f"Se sube con '{CLAVE_CONFIG[tipo]}' en config.json. "
            f"El contador vive en {_ruta_libro()} y se reinicia solo cada día."
        )

    consumo[tipo] += cantidad
    _escribir(consumo)
    return consumo


def consumo_de_hoy():
    return _leer()


def resumen_texto():
    """Una línea por tipo, para imprimir al final de la corrida."""
    consumo = _leer()
    partes = []
    for tipo in TIPOS:
        tope = _topes.get(tipo, TOPES_DEFAULT[tipo])
        partes.append(f"{tipo} {consumo[tipo]}/{tope}")
    modo = "PRUEBAS" if _modo_pruebas else "producción"
    return f"Consumo de hoy [{modo}]: " + ", ".join(partes)
