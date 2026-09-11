"""Formulaire public soumis en fetch() (cf. formulaire.html).

    python tests/test_formulaire.py

Écrit dans un dossier temporaire, ne touche jamais data/.
Le maintien des pièces après une erreur se joue DANS LE NAVIGATEUR (la page ne
recharge pas) ; côté serveur on vérifie le contrat JSON qui le permet :
  - POST fetch avec un champ obligatoire manquant -> 422 {ok:false} et AUCUNE
    soumission écrite sur le disque ;
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
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-formulaire-")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

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
    return len(list((config.DONNEES / "soumissions").glob("*/")))


def cas(nom, fn):
    fn()
    print(f"  ✓ {nom}")


def test_fetch_erreur_ne_cree_rien():
    c = app.test_client()
    avant = compte_soumissions()
    incomplet = {k: v for k, v in BASE.items() if k != "num_secu"}
    r = c.post("/", data={**incomplet, **pieces()}, **FETCH)
    assert r.status_code == 422, r.status_code
    d = r.get_json()
    assert d["ok"] is False and any("sécurité sociale" in e.lower() for e in d["erreurs"]), d
    assert compte_soumissions() == avant, "une soumission a été écrite malgré l'erreur"


def test_fetch_valide_cree_la_soumission():
    c = app.test_client()
    r = c.post("/", data={**BASE, **pieces()}, **FETCH)
    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert d["ok"] is True and d["titre"] == "Demande envoyée", d
    item = store.lire(max(i["id"] for i in store.tout()))
    assert len(store.fichiers_role(item, "pieces", "identite")) == 2, \
        store.fichiers(item, "pieces")


cas("POST fetch en erreur -> 422 {ok:false}, aucune soumission écrite", test_fetch_erreur_ne_cree_rien)
cas("POST fetch valide -> {ok:true}, soumission + 2 faces de CNI", test_fetch_valide_cree_la_soumission)
print("\nFormulaire fetch OK — erreur sans écriture, envoi valide, CNI en deux fichiers.")
