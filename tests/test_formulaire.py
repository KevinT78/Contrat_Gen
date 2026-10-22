"""Formulaire public soumis en fetch() (cf. formulaire.html).

    python tests/test_formulaire.py

Écrit dans une base vide (mongomock), rien sur le disque.
Le maintien des pièces après une erreur se joue DANS LE NAVIGATEUR (la page ne
recharge pas) ; côté serveur on vérifie le contrat JSON qui le permet :
  - POST fetch avec un champ obligatoire manquant -> 422 {ok:false} et AUCUNE
    soumission écrite en base ;
  - POST fetch valide -> {ok:true} + soumission créée (CNI en deux fichiers).
La génération du contrat à la validation est couverte par test_parcours*.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo  # noqa: E402
base_mongo("formulaire")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402


FETCH = {"headers": {"X-Requested-With": "contratgen-fetch"},
         "content_type": "multipart/form-data"}


def piece(nom="p.pdf"):
    return (io.BytesIO(b"%PDF-1.4\n%%EOF"), nom)


BASE = {"etablissement": "ACME Restauration / Lille Grand Place",
        "email_demandeur": "manager@example.com", "nom_naissance": "Martin",
        "nom_usage": "", "prenom": "Camille", "date_naissance": "1998-04-11",
        "lieu_naissance": "Lille", "nationalite": "Française",
        "num_secu": "2 98 04 59 350 042 21", "adresse": "14 rue Nationale",
        "poste": "Manager", "type_contrat": "CDI", "date_debut": "2026-10-01",
        "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450"}


def pieces():
    return {"identite": [piece("recto.pdf"), piece("verso.pdf")],
            "carte_vitale": piece(), "rib": piece()}


def compte_soumissions():
    return store.db().dossiers.count_documents({"zone": "soumissions"})


def cas(nom, fn):
    fn()
    print(f"  ✓ {nom}")


def test_fetch_erreur_ne_cree_rien():
    c = app.test_client()
    avant = compte_soumissions()
    incomplet = {k: v for k, v in BASE.items() if k != "num_secu"}
    r = c.post("/demande", data={**incomplet, **pieces()}, **FETCH)
    assert r.status_code == 422, r.status_code
    d = r.get_json()
    assert d["ok"] is False and any("sécurité sociale" in e.lower() for e in d["erreurs"]), d
    assert compte_soumissions() == avant, "une soumission a été écrite malgré l'erreur"


def test_fetch_valide_cree_la_soumission():
    c = app.test_client()
    avant = compte_soumissions()
    r = c.post("/demande", data={**BASE, **pieces()}, **FETCH)
    assert r.status_code == 200, r.status_code
    # contrôle du compteur : sans lui, les « rien d'écrit » ci-dessus passent à vide
    assert compte_soumissions() == avant + 1, "soumission valide non comptée"
    d = r.get_json()
    assert d["ok"] is True and d["titre"] == "Demande envoyée", d
    item = store.lire(max(i["id"] for i in store.tout()))
    assert len(store.fichiers_role(item, "pieces", "identite")) == 2, \
        store.fichiers(item, "pieces")


def test_date_saisie_en_jj_mm_aaaa():
    c = app.test_client()
    page = c.get("/demande").text
    assert 'name="date_naissance"' in page and 'placeholder="jj/mm/aaaa"' in page, \
        "le champ date doit être un texte jj/mm/aaaa (le natif suit la langue du navigateur)"
    r = c.post("/demande", data={**BASE, "date_naissance": "11/04/1998", **pieces()}, **FETCH)
    assert r.get_json()["ok"] is True, r.get_json()
    item = store.lire(max(i["id"] for i in store.tout()))
    assert item["champs"]["date_naissance"] == "1998-04-11", item["champs"]["date_naissance"]
    avant = compte_soumissions()
    r = c.post("/demande", data={**BASE, "date_naissance": "31/02/1998", **pieces()}, **FETCH)
    assert r.status_code == 422 and any("jj/mm/aaaa" in e for e in r.get_json()["erreurs"]), \
        r.get_json()
    assert compte_soumissions() == avant


def test_piece_lourde_jamais_sur_disque():
    """Au-dela de 500 Ko werkzeug spoole sur disque : le spool leve ici, et
    %TEMP% ne doit pas bouger. Sans request_class, le POST casse en 500."""
    import werkzeug.formparser as fp

    def interdit(*a, **k):
        raise AssertionError("fichier temporaire ouvert pour une piece deposee")

    def temp():
        return sorted(os.listdir(tempfile.gettempdir()))

    # Corps multipart a la main : l'encodeur du test client spoole lui-meme
    # sur disque au-dela de 500 Ko, ce qui fausserait le %TEMP% mesure.
    pdf = b"%PDF-1.4\n%%EOF"
    gros = b"%PDF-1.4\n" + b"x" * 1024 * 1024 + b"\n%%EOF"
    parts = [b'--B\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
             % (k.encode(), v.encode()) for k, v in BASE.items()]
    for champ, nom, octets in [("identite", "recto.pdf", pdf), ("identite", "verso.pdf", pdf),
                               ("carte_vitale", "cv.pdf", pdf), ("rib", "gros.pdf", gros)]:
        parts.append(b'--B\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                     b'Content-Type: application/pdf\r\n\r\n%s\r\n'
                     % (champ.encode(), nom.encode(), octets))
    corps = b"".join(parts) + b"--B--\r\n"
    avant, vrai = temp(), fp.SpooledTemporaryFile
    fp.SpooledTemporaryFile = interdit
    try:
        nb = compte_soumissions()
        r = app.test_client().post(
            "/demande", data=corps, headers=FETCH["headers"],
            content_type="multipart/form-data; boundary=B")
    finally:
        fp.SpooledTemporaryFile = vrai
    assert r.status_code == 200 and r.get_json()["ok"] is True, (r.status_code, r.data[:200])
    assert compte_soumissions() == nb + 1
    uid = max(i["id"] for i in store.tout())
    assert any(len(d["octets"]) > 1024 * 1024
               for d in store.db().fichiers.find({"uid": uid})), "la piece de 1 Mo n'est pas en base"
    assert temp() == avant, set(temp()) ^ set(avant)


def test_racine_mene_au_login():
    c = app.test_client()
    assert c.get("/").headers["Location"].endswith("/login"), "« / » doit mener au login"
    with c.session_transaction() as s:
        s["utilisateur"] = "rh"
    assert c.get("/").headers["Location"].endswith("/suivi"), "connecté, « / » mène au suivi"


cas("POST fetch en erreur -> 422 {ok:false}, aucune soumission écrite", test_fetch_erreur_ne_cree_rien)
cas("POST fetch valide -> {ok:true}, soumission + 2 faces de CNI", test_fetch_valide_cree_la_soumission)
cas("pièce de 1 Mo : aucun fichier temporaire, %TEMP% inchangé", test_piece_lourde_jamais_sur_disque)
cas("date saisie jj/mm/aaaa -> stockée AAAA-MM-JJ ; 31/02 refusé", test_date_saisie_en_jj_mm_aaaa)
cas("« / » -> login, ou suivi si connecté", test_racine_mene_au_login)
print("\nFormulaire fetch OK — erreur sans écriture, envoi valide, CNI en deux fichiers, dates jj/mm/aaaa, « / » vers le login.")
