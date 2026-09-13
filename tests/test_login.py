"""Durcissement du login : verrou anti-bourrinage, duree de vie de session,
et ProxyFix (sans lui les compteurs par IP deviennent un compteur global).

    python tests/test_login.py

Le piege de ce fichier : poster N+1 mauvais mots de passe et constater qu'on
n'est pas connecte PASSE MEME SANS LIMITEUR, puisque tout est refuse de toute
facon. La seule assertion qui distingue « le limiteur mord » de « le mot de
passe est faux » est de poster le BON mot de passe une fois le budget epuise.
Ecrit dans un dossier temporaire, ne touche jamais data/.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-login-")
os.environ["PROXIES"] = "1"        # ce fichier teste la config « derriere Caddy »

import config          # noqa: E402
import app as module   # noqa: E402
from app import app    # noqa: E402

app.config["PROPAGATE_EXCEPTIONS"] = True
for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

MDP = "fixture"


def pots_vides():
    """Les pots sont un etat de module partage entre les cas : sans ce reset,
    un cas echouerait pour le budget consomme par le precedent."""
    module._POT_LOGIN.clear()
    module._POT_PAR_IP.clear()


def tenter(client, ip, mdp):
    return client.post("/login", data={"identifiant": "rh", "mot_de_passe": mdp},
                       headers={"X-Forwarded-For": ip})


def connecte(client):
    return client.get("/suivi").status_code == 200


def le_verrou_mord():
    """Controle positif : une fois le budget epuise, meme le BON mot de passe
    ne connecte plus. C'est le seul cas qui vire au rouge si on retire la
    garde _autorise() de login()."""
    pots_vides()
    c = app.test_client()
    for _ in range(module._PLAFOND_LOGIN):
        tenter(c, "10.0.0.1", "mauvais")
    r = tenter(c, "10.0.0.1", MDP)
    assert r.status_code == 429, f"budget epuise mais reponse {r.status_code}"
    assert not connecte(c), "le bon mot de passe a connecte malgre le verrou"


def le_verrou_ne_bloque_pas_en_permanence():
    """Controle negatif : budget neuf, le bon mot de passe connecte du premier
    coup. Sans ce cas, un limiteur casse qui refuse TOUT passerait le cas
    precedent."""
    pots_vides()
    c = app.test_client()
    tenter(c, "10.0.0.2", MDP)
    assert connecte(c), "le bon mot de passe ne connecte pas sur un budget neuf"


def la_session_expire():
    """PERMANENT_SESSION_LIFETIME ne fait rien sans session.permanent : un
    cookie non permanent n'a ni Expires ni Max-Age."""
    pots_vides()
    c = app.test_client()
    r = tenter(c, "10.0.0.3", MDP)
    cookie = r.headers.get("Set-Cookie", "")
    assert "session=" in cookie, f"pas de cookie de session : {cookie!r}"
    assert "Expires=" in cookie or "Max-Age=" in cookie, \
        f"cookie sans duree de vie — session.permanent manquant ? {cookie!r}"


def proxyfix_separe_les_ip():
    """Sans ProxyFix, request.remote_addr vaut l'IP du proxy pour tout le
    monde : le budget devient global et la premiere IP verrouillee enferme
    dehors toutes les autres."""
    pots_vides()
    c = app.test_client()
    for _ in range(module._PLAFOND_LOGIN):
        tenter(c, "10.0.0.4", "mauvais")
    assert tenter(c, "10.0.0.4", "mauvais").status_code == 429, \
        "budget de la premiere IP non epuise, le cas ne prouve rien"
    r = tenter(c, "10.0.0.5", "mauvais")
    assert r.status_code != 429, \
        "une seconde IP est verrouillee par la premiere — ProxyFix absent ?"


def proxyfix_actif_sous_waitress():
    """Le cas precedent passe par test_client, qui contourne le serveur WSGI.
    Or waitress efface X-Forwarded-* par defaut avant l'app : ProxyFix n'a
    rien vu en production pendant que ce fichier etait vert (mesure derriere
    Caddy le 2026-09-13). Meme assertion, mais sur le vrai `python app.py`."""
    import socket
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    donnees = Path(tempfile.mkdtemp(prefix="contratgen-waitress-"))
    env = {**os.environ, "PORT": str(port), "PYTHONIOENCODING": "utf-8",
           "DONNEES": str(donnees)}
    env.pop("DEBUG", None)
    # sortie dans un fichier, pas un PIPE jamais relu : un pipe plein bloquerait le serveur
    journal = open(donnees / "app.log", "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "app.py"], env=env, stdout=journal,
                         stderr=subprocess.STDOUT,
                         cwd=str(Path(__file__).resolve().parent.parent))

    def tentative(ip):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/login", method="POST",
            data=b"identifiant=rh&mot_de_passe=mauvais",
            headers={"X-Forwarded-For": ip,
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            return urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        # « waitress sur » s'affiche AVANT que serve() n'ouvre le port : on
        # attend que le port reponde, borne dans le temps.
        fin = time.time() + 60
        while True:
            assert p.poll() is None, \
                "app.py s'est arrete :\n" + (donnees / "app.log").read_text(encoding="utf-8")
            assert time.time() < fin, "app.py n'ecoute toujours pas apres 60 s"
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        for _ in range(module._PLAFOND_LOGIN):
            tentative("10.9.9.1")
        assert tentative("10.9.9.1") == 429, \
            "budget de la premiere IP non epuise, le cas ne prouve rien"
        assert tentative("10.9.9.2") != 429, \
            "sous waitress, une seconde IP est verrouillee par la premiere : " \
            "X-Forwarded-For n'atteint pas l'app (clear_untrusted_proxy_headers ?)"
    finally:
        p.kill()
        p.wait(timeout=10)
        journal.close()




def xff_ignore_sans_proxy_declare():
    """Sans proxy devant, X-Forwarded-For est un en-tete FOURNI PAR LE CLIENT :
    le faire tourner contournerait les deux rate-limits. ProxyFix ne doit donc
    s'activer que si PROXIES le declare. En sous-process, car ProxyFix
    s'applique a l'import (fichier separe, meme patron que test_clients.py
    qui lance tests/_client_parcours.py)."""
    env = {k: v for k, v in os.environ.items() if k != "PROXIES"}
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("_login_sans_proxy.py"))],
                       env=env, capture_output=True, text=True, encoding="utf-8",
                       cwd=str(Path(__file__).resolve().parent.parent))
    assert r.returncode == 0, (r.stdout + r.stderr).strip()


def main():
    le_verrou_mord()
    le_verrou_ne_bloque_pas_en_permanence()
    la_session_expire()
    proxyfix_separe_les_ip()
    proxyfix_actif_sous_waitress()
    xff_ignore_sans_proxy_declare()
    print(f"LOGIN OK — verrou {module._PLAFOND_LOGIN}/{module._FENETRE_LOGIN}s, "
          f"session {app.config['PERMANENT_SESSION_LIFETIME']}, ProxyFix actif")


if __name__ == "__main__":
    main()
