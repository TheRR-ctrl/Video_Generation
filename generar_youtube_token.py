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

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUTA_CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
RUTA_TOKEN = os.path.join(BASE_DIR, "youtube_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    if not os.path.exists(RUTA_CLIENT_SECRET):
        raise SystemExit(
            f"Falta {RUTA_CLIENT_SECRET}. Descárgalo desde Google Cloud Console "
            "(OAuth client ID tipo 'Desktop app') y guárdalo en esta carpeta."
        )

    flow = InstalledAppFlow.from_client_secrets_file(RUTA_CLIENT_SECRET, SCOPES)
    print(
        "Se va a abrir tu navegador para autorizar el acceso. Si no se abre solo, "
        "copia el link que se imprime abajo y pégalo a mano:\n"
    )
    creds = flow.run_local_server(port=0, open_browser=True)

    with open(RUTA_TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"✅ Token guardado en {RUTA_TOKEN}")
    print("   Copia el contenido de ese archivo al secret YOUTUBE_TOKEN en GitHub.")


if __name__ == "__main__":
    main()
