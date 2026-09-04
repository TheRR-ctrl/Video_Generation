"""
Carga variables de entorno desde un archivo .env local (si existe) hacia
os.environ, sin pisar variables que ya estén definidas (por ejemplo, los
secrets que GitHub Actions ya inyecta como variables de entorno reales).

No requiere python-dotenv: en GitHub Actions no hay .env (las credenciales
llegan como secrets), así que este import es un no-op ahí.
"""
import os
from pathlib import Path

_RUTA_ENV = Path(__file__).resolve().parent / ".env"


def _cargar():
    if not _RUTA_ENV.exists():
        return
    for linea in _RUTA_ENV.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar()
