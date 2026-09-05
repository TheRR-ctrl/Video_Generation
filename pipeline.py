"""
Pipeline — orquesta las 4 etapas en una sola corrida:

  content_planner.py -> script_writer.py -> generar_video_maestro.py -> publisher.py

Cada etapa corre en su propio paso: si una falla, se registra el error pero
las etapas ya completadas (plan, guion, videos) quedan guardadas en disco y
la siguiente corrida puede retomar desde ahí (todas las etapas son
incrementales/idempotentes).

Uso:
  python pipeline.py                # corre las 4 etapas
  python pipeline.py --hasta guion  # corre solo plan + guion
  python pipeline.py --desde video  # corre solo video + publicar (asume
                                     # que guion.txt ya existe)
  python pipeline.py --forzar       # ignora el freno de sobreproducción

Freno de sobreproducción (tomado de video-scout-pipeline): publisher.py sube
como mucho max_subidas_por_corrida videos, así que generar sin parar acumula un
colchón que no se alcanza a publicar, gastando cuota de Gemini y tiempo de
render para nada. Antes de las etapas "plan" y "guion" se cuentan los videos ya
renderizados que siguen sin publicar ni rechazar; si llegan a
UMBRAL_BACKLOG_VIDEOS, esas dos etapas se saltan solas. --forzar la ignora.
"""
import sys
import argparse
import logging
import traceback

import env_local  # noqa: F401 (carga .env si existe, antes de cualquier otro import)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pipeline")

ETAPAS = ["plan", "guion", "video", "publicar"]
# Colchón de videos renderizados sin publicar a partir del cual se deja de
# generar contenido nuevo. Con una subida por corrida, cinco videos ya son
# varios días de publicaciones por delante.
UMBRAL_BACKLOG_VIDEOS = 5


def videos_pendientes_de_publicar():
    """Videos ya renderizados que no se subieron ni se rechazaron todavía.

    Ante cualquier problema devuelve 0: el freno es una optimización, y quedarse
    sin generar por no poder leer un archivo de estado sería peor que generar de
    más."""
    try:
        import publisher
        cfg = publisher.cargar_config()
        completados = publisher.cargar_json(publisher.RUTA_RESULTADO, {}).get("completados", [])
        procesados = (
            {p["ruta"] for p in publisher.cargar_json(publisher.RUTA_PUBLICADOS, [])}
            | {r["ruta"] for r in publisher.cargar_json(publisher.RUTA_RECHAZADOS, [])}
        )
        return len([v for v in completados if v["ruta"] not in procesados])
    except Exception as exc:
        logger.warning(f"No se pudo calcular el colchón pendiente ({exc}); no se frena la generación.")
        return 0


def correr_etapa(nombre, fn):
    logger.info(f"===== Etapa: {nombre} =====")
    try:
        fn()
        logger.info(f"Etapa '{nombre}' OK.")
        return True
    except SystemExit as exc:
        if exc.code not in (0, None):
            logger.error(f"Etapa '{nombre}' terminó con código {exc.code}.")
            return False
        return True
    except Exception:
        logger.error(f"Etapa '{nombre}' falló:\n{traceback.format_exc()}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Orquesta el pipeline completo.")
    parser.add_argument("--desde", choices=ETAPAS, default=ETAPAS[0])
    parser.add_argument("--hasta", choices=ETAPAS, default=ETAPAS[-1])
    parser.add_argument("--forzar", action="store_true",
                        help="genera contenido aunque haya colchón sin publicar")
    args = parser.parse_args()

    i_desde, i_hasta = ETAPAS.index(args.desde), ETAPAS.index(args.hasta)
    if i_desde > i_hasta:
        parser.error("--desde no puede ir después de --hasta")

    resultados = {}

    frena_generacion = False
    if not args.forzar and i_desde <= ETAPAS.index("guion"):
        pendientes = videos_pendientes_de_publicar()
        frena_generacion = pendientes >= UMBRAL_BACKLOG_VIDEOS
        if frena_generacion:
            logger.info(
                f"{pendientes} video(s) renderizados siguen sin publicar (umbral "
                f"{UMBRAL_BACKLOG_VIDEOS}): se saltan plan y guion. Usa --forzar para generar igual."
            )

    if not frena_generacion and i_desde <= ETAPAS.index("plan") <= i_hasta:
        import content_planner
        resultados["plan (content_planner)"] = correr_etapa("plan (content_planner)", content_planner.main)

    if not frena_generacion and i_desde <= ETAPAS.index("guion") <= i_hasta:
        import script_writer
        resultados["guion (script_writer)"] = correr_etapa("guion (script_writer)", script_writer.main)

    if i_desde <= ETAPAS.index("video") <= i_hasta:
        import generar_video_maestro
        resultados["video (generar_video_maestro)"] = correr_etapa(
            "video (generar_video_maestro)", generar_video_maestro.renderizar_lote_historias
        )

    if i_desde <= ETAPAS.index("publicar") <= i_hasta:
        import publisher
        resultados["publicar (publisher)"] = correr_etapa("publicar (publisher)", publisher.main)

    logger.info("===== Resumen =====")
    for nombre, ok in resultados.items():
        logger.info(f"  {'✅' if ok else '❌'} {nombre}")

    if not all(resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
