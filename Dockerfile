# Image de production de Contrat_Gen : waitress mono-process, derriere Caddy.
# L'image ne porte que le CODE. La config du client (CONFIG_DIR) est montee en
# lecture seule et les secrets arrivent par l'environnement (env_file) : rien
# de propre a un client n'entre dans l'image, qui est publique comme le depot.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt requirements-sharepoint.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-sharepoint.txt

COPY . .

# HOST=0.0.0.0 : dans le conteneur seulement. compose.yml ne publie le port que
# sur 127.0.0.1 de l'hote ; Caddy reste la seule porte d'entree.
ENV HOST=0.0.0.0 PORT=5000 CONFIG_DIR=/config
EXPOSE 5000

# /login repond 200 sans session : le serveur sert. demarrer() refuse deja de
# demarrer sans base joignable, donc « sain » vaut aussi « Atlas joignable ».
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/login' % os.environ['PORT'], timeout=4)"]

CMD ["python", "app.py"]
