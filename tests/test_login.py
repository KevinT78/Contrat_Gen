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
    xff_ignore_sans_proxy_declare()
    print(f"LOGIN OK — verrou {module._PLAFOND_LOGIN}/{module._FENETRE_LOGIN}s, "
          f"session {app.config['PERMANENT_SESSION_LIFETIME']}, ProxyFix actif")


if __name__ == "__main__":
    main()
