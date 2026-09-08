"""Chaque écran rend sans erreur, dans chacun des états du dossier.

    python tests/test_ecrans.py

La suite existante vérifie les transitions mais n'ouvre jamais GET /dossier :
une faute de template (fil d'étapes, badge, journal) passait inaperçue.
Écrit dans un dossier temporaire, ne touche jamais data/.
"""
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-ecrans-")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

app.config["PROPAGATE_EXCEPTIONS"] = True
for zone in ("soumissions", "documents"):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

ETAB = config.etablissements()[0][0]


def piece():
    return (BytesIO(b"%PDF-1.4\n%%EOF"), "p.pdf")


def saisie():
    return {"etablissement": ETAB, "email_demandeur": "manager@example.com",
            "nom_naissance": "Martin", "nom_usage": "", "prenom": "Camille",
            "date_naissance": "1998-04-11", "lieu_naissance": "Lille",
            "nationalite": "Française", "num_secu": "2 98 04 59 350 042 21",
            "adresse": "14 rue Nationale, 59000 Lille", "poste": "Manager",
            "type_contrat": "CDI", "date_debut": "2026-10-01",
            "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450"}


def ecran(c, url, attendu=200):
    r = c.get(url)
    assert r.status_code == attendu, f"{url} -> {r.status_code}"
    assert "{{" not in r.text, f"{url} : placeholder Jinja non rendu"
    return r.text


def soumettre(c):
    c.post("/", data={**saisie(), "identite": piece(), "carte_vitale": piece(),
                      "rib": piece()}, content_type="multipart/form-data")
    return max(i["id"] for i in store.tout())


def main():
    anonyme = app.test_client()
    ecran(anonyme, "/")                         # formulaire candidat
    ecran(anonyme, "/login")
    assert anonyme.get("/suivi").status_code == 302, "suivi ouvert sans session"

    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    ecran(c, "/suivi")
    ecran(c, "/suivi?inactifs=1")

    vus = set()
    uid = soumettre(c)
    fichier = {"content_type": "multipart/form-data"}

    def voir():
        e = store.etat(store.lire(uid))
        vus.add(e)
        texte = ecran(c, f"/dossier/{uid}")
        assert libelle(e) in texte, f"état « {e} » absent de l'écran"

    def libelle(e):
        from app import LIBELLES_ETAT
        return LIBELLES_ETAT[e]

    voir()                                                          # Soumise
    c.post(f"/dossier/{uid}/rejeter", data={"motif": "Pièce illisible ou manquante",
                                            "commentaire": "RIB flou"})
    voir()                                                          # Rejetee

    uid = soumettre(c)
    voir()
    c.post(f"/dossier/{uid}/valider")
    voir()                                                          # ATraiter
    c.post(f"/dossier/{uid}/contrat")
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()}, **fichier)
    voir()                                                          # ContratPret
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()}, **fichier)
    voir()                                                          # ContratSigne
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()}, **fichier)
    voir()                                                          # DpaeFaite
    c.post(f"/dossier/{uid}/remettre")
    voir()                                                          # RemisComptable

    item = store.lire(uid)
    jeton = store.signer("lot_comptable", uid, item.get("lien_comptable_epoch", 0))
    ecran(app.test_client(), f"/lot/{jeton}")                       # écran comptable

    uid = soumettre(c)
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/abandonner")
    voir()                                                          # Abandonnee

    manque = set(store.ETATS) - vus
    assert not manque, f"états jamais rendus : {sorted(manque)}"
    print(f"ÉCRANS OK — {len(vus)} états rendus, {config.DONNEES}")


if __name__ == "__main__":
    main()
