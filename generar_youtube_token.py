"""
Genera youtube_token.json mediante el flujo OAuth de Google, una sola vez.
El archivo resultante es el que copias al secret YOUTUBE_TOKEN de GitHub
Actions — no se sube nunca al repo.

Uso:
  python generar_youtube_token.py

Requiere client_secret.json en esta misma carpeta (ver README.md, sección
"Setup").
"""
import os
import sys
import subprocess
import webbrowser

import env_local  # noqa: F401 (carga .env si existe)

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _copiar_al_portapapeles(texto):
    """Intenta copiar 'texto' al portapapeles (Windows/macOS/Linux). No
    lanza si falla — es una ayuda extra, no algo de lo que dependa el flujo."""
    try:
        if sys.platform == "win32":
            cmd = ["clip"]
        elif sys.platform == "darwin":
            cmd = ["pbcopy"]
        else:
            cmd = ["xclip", "-selection", "clipboard"]
        subprocess.run(cmd, input=texto.encode("utf-8"), check=True, timeout=5)
        return True
    except Exception:
        return False


def _abrir_navegador_y_copiar(url, new=0, autoraise=True):
    """Reemplaza a webbrowser.open dentro de run_local_server: intenta abrir
    el navegador como siempre, y además copia el link al portapapeles para
    que baste con pegarlo (Ctrl+V) si el auto-open no funciona."""
    if _copiar_al_portapapeles(url):
        print("(Link copiado al portapapeles — pégalo con Ctrl+V si el navegador no se abrió solo.)\n")
    return webbrowser.open(url, new=new, autoraise=autoraise)


def main():
    if not os.path.exists(RUTA_CLIENT_SECRET):
        raise SystemExit(
            f"Falta {RUTA_CLIENT_SECRET}. Descárgalo desde Google Cloud Console "
            "(OAuth client ID tipo 'Desktop app') y guárdalo en esta carpeta."
        )

    flow = InstalledAppFlow.from_client_secrets_file(RUTA_CLIENT_SECRET, SCOPES)
    print(
        "Se va a abrir tu navegador para autorizar el acceso. Si no se abre solo, "
        "el link también queda copiado en tu portapapeles (pégalo con Ctrl+V) y "
        "además se imprime abajo:\n"
    )
    webbrowser.open = _abrir_navegador_y_copiar
    creds = flow.run_local_server(port=0, open_browser=True)

    with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"✅ Token guardado en {RUTA_TOKEN}")
    print("   Copia el contenido de ese archivo al secret YOUTUBE_TOKEN en GitHub.")


if __name__ == "__main__":
    main()
