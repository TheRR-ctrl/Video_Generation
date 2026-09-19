"""
Utilidades de archivos compartidas por todo el pipeline.

Existe por una sola razón: `valido()` estaba copiada, idéntica, en siete
módulos (los cinco motores de b-roll, el mezclador de audio y el maestro).
Es el chequeo que decide si un clip cacheado se reusa o se vuelve a generar,
así que si algún día hay que endurecerlo (por ejemplo, exigir un tamaño
mínimo para descartar un mp4 truncado por una corrida cortada a la mitad),
conviene que haya un solo lugar donde cambiarlo y no siete donde acordarse.
"""
import os


def valido(ruta):
    """True si la ruta apunta a un archivo que existe y no está vacío.

    Un archivo de cero bytes es el resultado típico de un render o una
    descarga que se cortó: existe, pero reusarlo rompe la etapa siguiente.
    """
    return bool(ruta) and os.path.isfile(ruta) and os.path.getsize(ruta) > 0
