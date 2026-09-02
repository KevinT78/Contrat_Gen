"""Driver : joue le parcours complet dans UNE copie du dépôt déjà installée.

    python tests/_client_parcours.py <racine-copie> <spec.json>

Lancé en sous-processus par tests/test_clients.py, une fois par client factice.
Sort 0 si le parcours passe, imprime « PARCOURS OK <client> <secret8> <donnees> ».
"""
import io
import json
import sys
import zipfile
from pathlib import Path

RACINE = sys.argv[1]
SPEC = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
sys.path.insert(0, RACINE)
sys.stdout.reconfigure(encoding="utf-8")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions", "documents"):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)


def f():
    return (io.BytesIO(b"%PDF-1.4\n%%EOF"), "p.pdf")


def saisie(etab, poste):
    return {
        "etablissement": etab, "email_demandeur": "manager@example.com",
        "nom_naissance": "Nkemba", "nom_usage": "", "prenom": "Awa",
        "date_naissance": "1996-03-07", "lieu_naissance": "Roubaix",
        "nationalite": "Française", "num_secu": "2 96 03 59 512 088 39",
        "adresse": "9 rue des Lilas", "poste": poste, "type_contrat": "CDI",
        "date_debut": "2026-12-01", "temps_partiel": "Non",
        "heures_hebdo": "35", "salaire": "2100",
    }


def couloir(c, etab, mode, poste, attendus):
    r = c.post("/", data={**saisie(etab, poste), "identite": f(),
                          "carte_vitale": f(), "rib": f()},
               content_type="multipart/form-data")
    assert "Demande envoyée" in r.text, r.text[:300]
    uid = max(i["id"] for i in store.tout())

    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert store.etat(item) == "ATraiter", store.etat(item)
    if config.instance().get("fiche_salarie"):
        assert "fiche-salarie.docx" in store.fichiers(item, "contrat"), "fiche absente"

    if mode == "genere":
        c.post(f"/dossier/{uid}/contrat")
        item = store.lire(uid)
        assert store.etat(item) == "ContratPret", item["journal"][-1]
        from docx import Document
        import contrat as moteur
        doc = Document(str(Path(item["_dir"]) / "contrat" / "contrat.docx"))
        texte = "\n".join(p.text for p in moteur.paragraphes(doc))
        assert "{{" not in texte, "contrat troué"
        for a in attendus:
            assert a in texte, f"« {a} » absent du contrat de {etab}"
    else:
        r = c.post(f"/dossier/{uid}/contrat", follow_redirects=True)
        assert store.etat(store.lire(uid)) == "ATraiter", "généré sur un étab. déposé"
        c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": f()},
               content_type="multipart/form-data")
        assert store.etat(store.lire(uid)) == "ContratPret"

    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": f()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": f()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"

    jeton = store.signer("lot_comptable", uid, item.get("lien_comptable_epoch", 0))
    z = zipfile.ZipFile(io.BytesIO(app.test_client().get(f"/lot/{jeton}/zip").data))
    assert "contrat/contrat-signe.pdf" in z.namelist(), z.namelist()
    return uid


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": SPEC["password"]})
    assert c.get("/suivi").status_code == 200, "login RH échoué"

    couloir(c, SPEC["etab_genere"], "genere", SPEC["poste"], SPEC["attendus"])
    if SPEC.get("etab_depose"):
        couloir(c, SPEC["etab_depose"], "depose", SPEC["poste"], [])

    inst = config.instance()
    print(f"PARCOURS OK\t{inst['client']}\t{inst['secret'][:8]}\t{config.DONNEES}")


if __name__ == "__main__":
    main()
