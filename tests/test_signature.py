"""signature.py (e-sign Yousign) contre un transport factice — aucun réseau.

    python tests/test_signature.py

Vérifie l'enchaînement create -> upload -> signer -> activate, puis le polling :
recuperer() rend None tant que la procédure n'est pas « done », les octets du
PDF signé ensuite.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo  # noqa: E402
base_mongo("signature")

import config          # noqa: E402
import signature       # noqa: E402

config.instance()["signature"] = {"mode": "yousign", "url": "https://faux.yousign/v3"}
os.environ["YOUSIGN_CLE_API"] = "k-test"        # un secret : l'environnement, pas instance.json

appels = []
_etat = {"done": False}


def faux_transport(methode, url, entetes, corps):
    appels.append(f"{methode} {url.split('/v3')[1]}")
    assert entetes["Authorization"] == "Bearer k-test"
    chemin = url.split("/v3")[1]
    if methode == "POST" and chemin == "/signature_requests":
        return 201, json.dumps({"id": "req1"}).encode()
    if chemin == "/signature_requests/req1/documents":
        assert b"signable_document" in corps and b"%PDF" in corps
        return 201, json.dumps({"id": "doc1"}).encode()
    if chemin == "/signature_requests/req1/signers":
        assert json.loads(corps)["info"]["email"] == "cand@example.com"
        return 201, b"{}"
    if chemin == "/signature_requests/req1/activate":
        return 201, b"{}"
    if chemin == "/signature_requests/req1" and methode == "GET":
        return 200, json.dumps(
            {"status": "done" if _etat["done"] else "ongoing",
             "documents": [{"id": "doc1"}]}).encode()
    if chemin.startswith("/signature_requests/req1/documents/doc1/download"):
        return 200, b"%PDF-1.4 signed"
    raise AssertionError(f"route inattendue : {methode} {chemin}")


signature._TRANSPORT = faux_transport

docx = Path(tempfile.mkstemp(suffix=".docx")[1])
docx.write_bytes(b"%PDF-fake-docx-bytes")

pid = signature.envoyer(docx.read_bytes(), {"prenom": "Cam", "nom": "Martin",
                                            "email": "cand@example.com"})
assert pid == "req1", pid
assert appels == [
    "POST /signature_requests",
    "POST /signature_requests/req1/documents",
    "POST /signature_requests/req1/signers",
    "POST /signature_requests/req1/activate",
], appels

assert signature.recuperer("req1") is None, "PDF rendu avant la fin de la procédure"
_etat["done"] = True
assert signature.recuperer("req1") == b"%PDF-1.4 signed"



def base_coupee_apres_envoi():
    """Yousign a créé et activé la procédure (le salarié a son mail), puis la
    base tombe avant d'avoir noté la procédure au dossier. La RH doit lire
    l'identifiant et la consigne de ne pas renvoyer, pas la page générale
    « vérifiez le dossier avant de refaire » : le dossier n'en garde rien et
    un second envoi doublerait la demande de signature."""
    import io
    from pymongo.errors import AutoReconnect
    import store
    from app import app
    from _outils import compte
    compte("rh", "fixture")
    secure = app.config["SESSION_COOKIE_SECURE"]
    app.config["SESSION_COOKIE_SECURE"] = False
    activations = []

    def yousign(methode, url, entetes, corps):
        chemin = url.split("/v3")[1]
        if chemin.endswith("/activate"):
            activations.append(chemin)
        return 201, json.dumps({"id": "proc-42"}).encode()
    signature._TRANSPORT = yousign

    pdf = lambda: (io.BytesIO(b"%PDF-1.4\n%%EOF"), "p.pdf")
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    r = c.post("/demande", content_type="multipart/form-data", data={
        "etablissement": "ACME Restauration / Lille Grand Place",
        "email_demandeur": "manager@example.com", "email": "cand@example.com",
        "nom_naissance": "Martin", "nom_usage": "", "prenom": "Cam",
        "date_naissance": "1991-07-02", "lieu_naissance": "Paris",
        "nationalite": "Française", "num_secu": "1 91 07 75 116 001 88",
        "adresse": "1 rue A, 75001 Paris", "poste": "Manager", "type_contrat": "CDI",
        "date_debut": "2026-10-01", "temps_partiel": "Non", "heures_hebdo": "35",
        "salaire": "2600", "identite": [pdf(), pdf()], "carte_vitale": pdf(), "rib": pdf()})
    assert "Demande envoyée" in r.text, r.status_code
    uid = max(i["id"] for i in store.tout())
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": pdf()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{uid}/remettre")
    assert store.signe_manquant(store.lire(uid)), store.etat(store.lire(uid))

    vrai = store.noter

    def noter_sans_base(uid_, **entree):
        if entree.get("type") == "signature_envoyee":
            raise AutoReconnect("base injoignable")
        return vrai(uid_, **entree)
    store.noter = noter_sans_base
    try:
        r = c.post(f"/dossier/{uid}/signature-envoyer")
    finally:
        store.noter = vrai
        signature._TRANSPORT = faux_transport
        app.config["SESSION_COOKIE_SECURE"] = secure
    page = r.get_data(as_text=True)
    assert activations, "contrôle : la procédure a bien été activée chez Yousign"
    assert "proc-42" in page and "ne le renvoyez pas" in page, (r.status_code, page[:600])
    assert "Base de données indisponible" not in page, "page générale au lieu du message"


base_coupee_apres_envoi()

# mode manuel -> refus net
config.instance()["signature"] = {"mode": "manuel"}
try:
    signature.envoyer(docx.read_bytes(), {"email": "x@example.com"})
    raise AssertionError("envoyer() accepté en mode manuel")
except RuntimeError:
    pass

print("signature.py OK — transport factice, polling, garde du mode")
