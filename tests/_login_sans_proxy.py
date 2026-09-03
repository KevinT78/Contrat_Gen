"""Sous-process de tests/test_login.py : PROXIES non declare.

Sans proxy devant, X-Forwarded-For est un en-tete FOURNI PAR LE CLIENT.
ProxyFix ne doit donc pas s'activer, sinon le faire tourner contourne
entierement le plafond. Fichier separe et non chaine inline, comme
tests/_client_parcours.py : ProxyFix s'applique a l'import, il faut donc un
interpreteur neuf pour exercer l'autre reglage.

Sort 0 si le plafond est bien atteint malgre l'en-tete tournant.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-noproxy-")
os.environ.pop("PROXIES", None)

import config          # noqa: E402
import app as module   # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions", "documents"):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

c = app.test_client()
vus = set()
for i in range(module._PLAFOND_LOGIN * 3 + 5):
    r = c.post("/login", data={"identifiant": "rh", "mot_de_passe": "faux"},
               headers={"X-Forwarded-For": "203.0.113.%d" % i})
    vus.add(r.status_code)
    if r.status_code == 429:
        sys.exit(0)

sys.exit("X-Forwarded-For tournant a contourne le plafond alors que PROXIES "
         "n'est pas declare : ProxyFix est actif a tort (codes vus : %s)" % sorted(vus))
