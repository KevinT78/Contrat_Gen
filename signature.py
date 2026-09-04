"""E-signature par un prestataire (Yousign API v3). Optionnel, désactivé par
défaut : sans bloc "signature" en mode "yousign" dans config/instance.json, le
parcours reste en signature manuelle (la RH dépose le PDF signé elle-même).

    "signature": {
      "mode": "yousign",
      "cle_api": "...",
      "url": "https://api.yousign.com/v3"      # optionnel, défaut ci-dessous
    }

Simplification volontaire : polling par bouton (recuperer), pas de webhook —
pas d'endpoint public à exposer ni de secret de callback. À revoir si l'attente
devient un irritant.

Un seul prestataire, pas d'abstraction à une implémentation. `_TRANSPORT` est
le seul point d'injection : les tests y branchent un transport factice.
"""
import json
import urllib.request
import uuid

import config

URL_DEFAUT = "https://api.yousign.com/v3"
_TRANSPORT = None        # test hook : (methode, url, headers, corps) -> (statut, bytes)


def _conf():
    s = config.instance().get("signature", {})
    if s.get("mode") != "yousign":
        raise RuntimeError("signature e-sign non activée (mode != 'yousign')")
    return s.get("url") or URL_DEFAUT, s["cle_api"]


def _appel(methode, chemin, cle, corps=b"", type_contenu="application/json"):
    base, _ = _conf()
    url = base.rstrip("/") + chemin
    entetes = {"Authorization": f"Bearer {cle}", "Accept": "application/json"}
    if corps:
        entetes["Content-Type"] = type_contenu
    if _TRANSPORT:
        statut, brut = _TRANSPORT(methode, url, entetes, corps)
    else:                                                    # pragma: no cover
        req = urllib.request.Request(url, data=corps or None, headers=entetes, method=methode)
        with urllib.request.urlopen(req, timeout=30) as r:
            statut, brut = r.status, r.read()
    if statut >= 300:
        raise RuntimeError(f"Yousign {methode} {chemin} -> {statut} : {brut[:300]!r}")
    return brut


def _multipart(contrat_octets):
    """Corps multipart minimal : file + nature=signable_document."""
    limite = uuid.uuid4().hex
    tete = (f'--{limite}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="contrat.docx"\r\nContent-Type: application/octet-stream\r\n\r\n')
    milieu = (f'\r\n--{limite}\r\nContent-Disposition: form-data; name="nature"\r\n\r\n'
              f'signable_document\r\n--{limite}--\r\n')
    corps = tete.encode() + contrat_octets + milieu.encode()
    return corps, f"multipart/form-data; boundary={limite}"


def envoyer(contrat_octets, signataire):
    """Crée la procédure, joint le contrat, ajoute le signataire, active.
    `contrat_octets` : le document à signer (bytes, lu via store.ouvrir).
    `signataire` : {"prenom", "nom", "email"}. -> id de procédure."""
    _, cle = _conf()
    pid = json.loads(_appel("POST", "/signature_requests", cle,
                            json.dumps({"name": f"Contrat {signataire.get('nom', '')}".strip(),
                                        "delivery_mode": "email"}).encode()))["id"]
    corps, tc = _multipart(contrat_octets)
    did = json.loads(_appel("POST", f"/signature_requests/{pid}/documents", cle,
                            corps, tc))["id"]
    _appel("POST", f"/signature_requests/{pid}/signers", cle, json.dumps({
        "info": {"first_name": signataire.get("prenom", ""),
                 "last_name": signataire.get("nom", ""),
                 "email": signataire["email"], "locale": "fr"},
        "signature_level": "electronic_signature",
        "signature_authentication_mode": "no_otp",
        "fields": [{"type": "signature", "document_id": did, "page": 1, "x": 200, "y": 600}],
    }).encode())
    _appel("POST", f"/signature_requests/{pid}/activate", cle)
    return pid


def recuperer(id_procedure):
    """-> PDF signé (bytes) si la procédure est terminée, sinon None."""
    _, cle = _conf()
    proc = json.loads(_appel("GET", f"/signature_requests/{id_procedure}", cle))
    if proc.get("status") != "done":
        return None
    did = proc["documents"][0]["id"]
    return _appel("GET", f"/signature_requests/{id_procedure}/documents/{did}/download"
                         "?version=completed", cle)
