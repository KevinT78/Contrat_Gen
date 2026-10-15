"""Zéro disque : aucune route qui reçoit un fichier n'ouvre de fichier temporaire.

    python tests/test_zero_disque.py

Le spool de werkzeug est piégé (il lève) et chaque dépôt dépasse son seuil de
500 Ko. Couvre les routes qui lisent request.files : formulaire public,
correction par lien, contrat déposé, contrat signé, accusé DPAE, import Excel.
Un corps multipart écrit à la main : l'encodeur du test client spoole lui-même.
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo  # noqa: E402
base_mongo("zero_disque")

import werkzeug.formparser as fp   # noqa: E402
import config          # noqa: E402
import store           # noqa: E402
from app import app, WAITRESS    # noqa: E402
from _outils import compte, mails, corps_texte  # noqa: E402
compte("rh", "fixture")

ETAB = "ACME Sud / Marseille Prado"
GROS = b"%PDF-1.4\n" + b"x" * 700 * 1024 + b"\n%%EOF"
PETIT = b"%PDF-1.4\n%%EOF"
SAISIE = {"etablissement": ETAB, "email_demandeur": "manager@example.com",
          "nom_naissance": "Martin", "nom_usage": "", "prenom": "Camille",
          "date_naissance": "1998-04-11", "lieu_naissance": "Lille",
          "nationalite": "Française", "num_secu": "2 98 04 59 350 042 21",
          "adresse": "14 rue Nationale", "poste": "Équipier polyvalent",
          "type_contrat": "CDI", "date_debut": "2026-10-01",
          "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450"}


def corps(champs, fichiers):
    """(corps, content_type) multipart ; fichiers = [(champ, nom, octets)]."""
    morceaux = [b'--B\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (k.encode(), v.encode()) for k, v in champs.items()]
    morceaux += [b'--B\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 b'Content-Type: application/pdf\r\n\r\n%s\r\n'
                 % (c.encode(), n.encode(), o) for c, n, o in fichiers]
    return b"".join(morceaux) + b"--B--\r\n", "multipart/form-data; boundary=B"


def interdit(*a, **k):
    raise AssertionError("fichier temporaire ouvert pour un fichier déposé")


def poster(c, url, champs, fichiers, **kw):
    """POST sous spool piégé ; le %TEMP% ne doit pas bouger."""
    avant = set(os.listdir(tempfile.gettempdir()))
    vrai, fp.SpooledTemporaryFile = fp.SpooledTemporaryFile, interdit
    try:
        data, ctype = corps(champs, fichiers)
        r = c.post(url, data=data, content_type=ctype,
                   headers={"X-Requested-With": "contratgen-fetch"}, **kw)
    finally:
        fp.SpooledTemporaryFile = vrai
    assert r.status_code != 500, (url, r.status_code, r.data[:300])
    nouveaux = {f for f in set(os.listdir(tempfile.gettempdir())) - avant
                if not f.endswith(".tmp")}
    assert not nouveaux, (url, nouveaux)
    return r


def octets_en_base(uid, role):
    return max((len(d["octets"]) for d in store.db().fichiers.find({"uid": uid})
                if role in d["nom"]), default=0)


def cas(nom, fn):
    fn()
    print(f"  ✓ {nom}")


c = app.test_client()
assert c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"}).status_code == 302
PIECES = [("identite", "recto.pdf", PETIT), ("identite", "verso.pdf", PETIT),
          ("carte_vitale", "cv.pdf", PETIT), ("rib", "rib.pdf", PETIT)]


def depot_public():
    r = poster(app.test_client(), "/", SAISIE, PIECES[:3] + [("rib", "gros.pdf", GROS)])
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.data[:300]
    uid = max(i["id"] for i in store.tout())
    assert any(len(d["octets"]) > 500 * 1024 for d in store.db().fichiers.find({"uid": uid}))
    return uid


def contrat_depose_signe_dpae(uid):
    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ATraiter"
    poster(c, f"/dossier/{uid}/contrat-depose", {}, [("contrat", "contrat.pdf", GROS)])
    assert store.etat(store.lire(uid)) == "ContratPret", store.lire(uid)["journal"][-1]
    poster(c, f"/dossier/{uid}/dpae-faite", {}, [("accuse", "accuse.pdf", GROS)])
    assert store.etat(store.lire(uid)) == "DpaeFaite", store.lire(uid)["journal"][-1]
    c.post(f"/dossier/{uid}/remettre")
    assert store.etat(store.lire(uid)) == "RemisComptable"
    poster(c, f"/dossier/{uid}/contrat-signe", {}, [("signe", "signe.pdf", GROS)])
    assert store.fichiers_role(store.lire(uid), "contrat", "contrat-signe"), \
        "contrat signé non enregistré"


def correction_par_lien():
    uid = depot_public()
    c.post(f"/dossier/{uid}/rejeter", data={"motif": "Pièce illisible ou manquante",
                                             "commentaire": "RIB flou"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    mail = mails("rejet")[-1]
    lien = re.search(r"http://localhost(/corriger/[\w.=-]+)", corps_texte(mail)).group(1)
    r = poster(app.test_client(), lien, SAISIE, [("rib", "gros.pdf", GROS)])
    assert r.get_json()["titre"] == "Correction envoyée", r.data[:300]
    assert store.etat(store.lire(uid)) != "Rejetee", "correction non enregistrée"


def excel_lourd():
    config.sharepoint = lambda: {"x": 1}
    r = poster(c, f"/etablissements/completer/excel?cle={ETAB}", {"cle": ETAB},
               [("fichiers", "gros.xlsx", GROS)])
    assert r.status_code in (200, 302), r.status_code


def sous_waitress():
    """Le serveur de prod, avec les réglages de WAITRESS : waitress spoole
    lui-même les corps > 512 Ko et les réponses > 1 Mo, avant Flask. Sans eux
    ce test lève (TemporaryFile espionné)."""
    import threading
    import urllib.request
    from waitress import create_server
    ouverts = []
    vrai = tempfile.TemporaryFile
    secure = app.config["SESSION_COOKIE_SECURE"]
    app.config["SESSION_COOKIE_SECURE"] = False   # le test parle en http
    tempfile.TemporaryFile = lambda *a, **k: ouverts.append(1) or vrai(*a, **k)
    srv = create_server(app, host="127.0.0.1", port=0, **WAITRESS)
    threading.Thread(target=srv.run, daemon=True).start()
    base = f"http://127.0.0.1:{srv.effective_port}"
    try:
        aleatoire = PETIT + os.urandom(1200 * 1024)   # incompressible : le zip fait > 1 Mo
        data, ctype = corps(SAISIE, PIECES[:3] + [("rib", "gros.pdf", aleatoire)])
        req = urllib.request.Request(base + "/", data=data, method="POST",
                                     headers={"Content-Type": ctype,
                                              "X-Requested-With": "contratgen-fetch"})
        assert b'"ok":true' in urllib.request.urlopen(req).read().replace(b" ", b"")
        # reponse > 1 Mo : l'export zip d'un dossier portant une piece de 700 Ko
        # fait ~700 Ko ; on force > 1 Mo avec une page servie plusieurs fois.
        uid = max(i["id"] for i in store.tout())
        cj = urllib.request.HTTPCookieProcessor()
        op = urllib.request.build_opener(cj)
        op.open(urllib.request.Request(
            base + "/login", method="POST",
            data=b"identifiant=rh&mot_de_passe=fixture")).read()
        z = op.open(base + f"/dossier/{uid}/export").read()
        assert z[:2] == b"PK" and len(z) > 1024 * 1024, (len(z), z[:200])
    finally:
        tempfile.TemporaryFile = vrai
        app.config["SESSION_COOKIE_SECURE"] = secure
        srv.close()
    assert not ouverts, f"waitress a ouvert {len(ouverts)} fichier(s) temporaire(s)"


def connexions_bornees():
    """Chaque connexion peut tenir 64 Mo de tampon en RAM : WAITRESS borne leur
    nombre. Une fois la limite tenue par des connexions lentes, la suivante
    attend ; les connexions fermées rendent la place."""
    import socket
    import threading
    import urllib.request
    from waitress import create_server
    srv = create_server(app, host="127.0.0.1", port=0, **WAITRESS)
    threading.Thread(target=srv.run, daemon=True).start()
    base = f"http://127.0.0.1:{srv.effective_port}"
    lentes = []
    try:
        with urllib.request.urlopen(base + "/login", timeout=5) as r:
            assert r.status == 200 and r.read()
        # 18 = 20 - les 2 sockets internes que waitress compte avec les clients :
        # la limite tout juste atteinte. connection_limit à 21 servirait la suivante.
        for _ in range(18):
            s = socket.create_connection(("127.0.0.1", srv.effective_port))
            s.sendall(b"GET /login HTTP/1.1\r\nHost: x\r\n")   # en-têtes jamais finis
            lentes.append(s)
        time.sleep(1)
        suivante = socket.create_connection(("127.0.0.1", srv.effective_port))
        lentes.append(suivante)
        suivante.sendall(b"GET /login HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        suivante.settimeout(2)
        try:
            suivante.recv(64)
            raise AssertionError("connexion servie au-delà de connection_limit")
        except TimeoutError:
            pass
        while len(lentes) > 1:
            lentes.pop(0).close()
        suivante.settimeout(10)
        reponse = b""
        while bloc := suivante.recv(65536):   # jusqu'au bout : srv.close() pendant
            reponse += bloc                    # l'écriture trace une erreur waitress
        assert reponse.startswith(b"HTTP/1.1 200"), \
            "la connexion en attente n'a pas été servie une fois les places rendues"
    finally:
        for s in lentes:
            s.close()
        srv.close()


cas("formulaire public : pièce de 700 Ko en base, aucun fichier temporaire", depot_public)
cas("contrat déposé, contrat signé, accusé DPAE de 700 Ko", lambda: contrat_depose_signe_dpae(depot_public()))
cas("correction par lien avec pièce de 700 Ko", correction_par_lien)
cas("import Excel de 700 Ko : lu en mémoire", excel_lourd)
cas("waitress réel : corps de 700 Ko reçu et export zip servi sans fichier temporaire", sous_waitress)
cas("waitress réel : connexions bornées, la suivante attend une place", connexions_bornees)
print("\nZéro disque OK — spool piégé, %TEMP% inchangé sur toutes les routes de dépôt.")
