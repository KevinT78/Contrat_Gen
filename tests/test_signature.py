"""signature.py (e-sign Yousign) contre un transport factice — aucun réseau.

    python tests/test_signature.py

Vérifie l'enchaînement create -> upload -> signer -> activate, puis le polling :
recuperer() rend None tant que la procédure n'est pas « done », les octets du
PDF signé ensuite.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config          # noqa: E402
import signature       # noqa: E402

config.instance()["signature"] = {"mode": "yousign", "cle_api": "k-test",
                                  "url": "https://faux.yousign/v3"}

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

# mode manuel -> refus net
config.instance()["signature"] = {"mode": "manuel"}
try:
    signature.envoyer(docx.read_bytes(), {"email": "x@example.com"})
    raise AssertionError("envoyer() accepté en mode manuel")
except RuntimeError:
    pass

print("signature.py OK — transport factice, polling, garde du mode")
