"""Le parcours complet, les deux couloirs du schéma, de bout en bout.

    python tests/test_parcours.py

Écrit dans un dossier temporaire, ne touche jamais data/.
  - couloir Dark Kitchen : Lille Grand Place, contrat généré
  - couloir Restaurant   : Marseille Prado, contrat déposé (myrhis)
Les deux passent par ContratSigne. Fiche salarié générée dans contrat/ dès la
validation. recap.py liste le salarié de la semaine. Plus les refus attendus.
"""
import email
import os
import re
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEMP = tempfile.mkdtemp(prefix="contratgen-test-")
os.environ["DONNEES"] = TEMP

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions", "documents"):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF", "p.pdf")


def piece():
    return (BytesIO(PDF[0]), PDF[1])


def base_saisie(etab, poste="Manager"):
    return {
        "etablissement": etab,
        "email_demandeur": "manager@example.com",
        "nom_naissance": "Martin", "nom_usage": "", "prenom": "Camille",
        "date_naissance": "1998-04-11", "lieu_naissance": "Lille",
        "nationalite": "Française", "num_secu": "2 98 04 59 350 042 21",
        "adresse": "14 rue Nationale, 59000 Lille",
        "poste": poste, "type_contrat": "CDI", "date_debut": "2026-10-01",
        "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450",
    }


def dernier_mail(nom):
    fichiers = sorted((config.DONNEES / "mails").glob(f"*-{nom}.eml"))
    assert fichiers, f"aucun mail « {nom} » envoye"
    msg = email.message_from_bytes(fichiers[-1].read_bytes())
    return msg["To"] + "\n" + msg.get_payload(decode=True).decode("utf-8")


def lien_dans(mail, chemin):
    m = re.search(rf"(http://localhost/{chemin}/[\w.=-]+)", mail)
    assert m, f"pas de lien /{chemin} dans le mail :\n{mail}"
    return m.group(1)


def soumettre(c, saisie):
    r = c.post("/", data={**saisie, "identite": piece(), "carte_vitale": piece(),
                          "rib": piece()}, content_type="multipart/form-data")
    assert r.status_code == 200 and "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())      # ULID le plus récent


def couloir_dark_kitchen(c):
    """Lille Grand Place — contrat généré par l'app, signature manuelle."""
    uid = soumettre(c, base_saisie("Wingstop France / Lille Grand Place"))

    # KO puis correction
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante", "commentaire": "RIB flou"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    lien = lien_dans(dernier_mail("rejet"), "corriger")
    r = c.post(lien, data={**base_saisie("Wingstop France / Lille Grand Place"),
                           "rib": piece()}, content_type="multipart/form-data")
    assert "Correction envoyée" in r.text
    assert c.get(lien).status_code == 410, "lien de correction encore vivant"
    assert (Path(store.lire(uid)["_dir"]) / "_versions").is_dir(), "re-dépôt non archivé"

    # validation -> dossier + fiche salarié dans contrat/
    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert item["_zone"] == "documents" and store.etat(item) == "ATraiter"
    assert "fiche-salarie.docx" in store.fichiers(item, "contrat"), \
        "fiche salarié absente de contrat/ après validation"
    assert any(e.get("type") == "fiche_salarie" for e in item["journal"])

    # refus : RemisComptable direct depuis ATraiter (garde de store.TRANSITIONS)
    try:
        store.transition(uid, "RemisComptable", "test")
        assert False, "transition ATraiter -> RemisComptable acceptée"
    except ValueError:
        pass

    # contrat généré
    c.post(f"/dossier/{uid}/contrat")
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert item["journal"][-1]["modele"] == "CDI_Manager.docx"
    from docx import Document
    import contrat as moteur
    doc = Document(str(Path(item["_dir"]) / "contrat" / "contrat.docx"))
    texte = "\n".join(p.text for p in moteur.paragraphes(doc))
    assert "{{" not in texte, "contrat troué"
    for attendu in ("Camille MARTIN", "1er octobre 2026", "2450",
                    "WINGSTOP FRANCE SAS", "884 512 336 00027", "Lille Grand Place"):
        assert attendu in texte, f"« {attendu} » absent du contrat"

    # refus : DPAE tant que le contrat n'est pas signé
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret", "DPAE acceptée avant signature"

    # signature manuelle : dépôt du PDF signé
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne"

    # rappel DPAE puis DPAE faite
    c.post(f"/dossier/{uid}/rappel-dpae")
    assert store.etat(store.lire(uid)) == "RappelDpae"
    rappel = dernier_mail("rappel_dpae")
    assert "Martin" in rappel and "http://localhost/dossier/" in rappel
    c.post(f"/dossier/{uid}/dpae-faite", data={}, content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "RappelDpae", "DPAE validée sans accusé"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"

    # remise : le lot porte contrat, contrat signé, fiche salarié, accusé
    c.post(f"/dossier/{uid}/remettre")
    assert store.etat(store.lire(uid)) == "RemisComptable"
    mail = dernier_mail("avis_comptable")
    assert "cabinet-nord@example.com" in mail
    lot = lien_dans(mail, "lot")
    anonyme = app.test_client()
    z = zipfile.ZipFile(BytesIO(anonyme.get(lot + "/zip").data))
    assert sorted(z.namelist()) == [
        "contrat/accuse-dpae.pdf", "contrat/contrat-signe.pdf", "contrat/contrat.docx",
        "contrat/fiche-salarie.docx", "pieces/carte-vitale.pdf",
        "pieces/identite.pdf", "pieces/rib.pdf"], z.namelist()

    # Le lot est public (possession du lien = accès) et un contrat peut être un
    # .html rempli de valeurs venues du formulaire public, sans échappement :
    # servi inline ce serait du script sur l'origine de l'app.
    r = anonyme.get(f"{lot}/fichier/contrat/contrat.docx")
    assert "attachment" in r.headers.get("Content-Disposition", ""), r.headers
    assert r.headers.get("X-Content-Type-Options") == "nosniff", r.headers
    apercu = anonyme.get(f"{lot}/fichier/pieces/identite.pdf")
    assert "attachment" not in apercu.headers.get("Content-Disposition", ""), \
        "les pièces restent en aperçu"

    # renvoi : l'ancien lien meurt
    c.post(f"/dossier/{uid}/renvoyer")
    assert anonyme.get(lot).status_code == 410
    return uid


def couloir_restaurant(c):
    """Marseille Prado — contrat fait sur myrhis puis déposé."""
    uid = soumettre(c, base_saisie("Wingstop Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ATraiter"

    # refus : « Générer le contrat » sur un établissement en mode déposé
    r = c.post(f"/dossier/{uid}/contrat", follow_redirects=True)
    assert store.etat(store.lire(uid)) == "ATraiter", "contrat généré sur un établissement déposé"
    assert "contrat déposé" in r.text or "myrhis" in r.text

    # dépôt du contrat -> ContratPret
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert "contrat.pdf" in store.fichiers(item, "contrat")

    # signature manuelle puis DPAE directe (sans rappel)
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    assert "cabinet-sud@example.com" in dernier_mail("avis_comptable")
    return uid


def fiche_absente_si_non_declaree(c):
    """Sans "fiche_salarie" dans instance.json, aucune fiche n'est produite."""
    garde = config.instance().pop("fiche_salarie", None)
    try:
        uid = soumettre(c, base_saisie("Wingstop France / Paris Opéra"))
        c.post(f"/dossier/{uid}/valider")
        assert "fiche-salarie.docx" not in store.fichiers(store.lire(uid), "contrat")
    finally:
        if garde:
            config.instance()["fiche_salarie"] = garde


def recap_liste_la_semaine(uids):
    import recap
    seuil = recap.datetime.now(recap.timezone.utc) - recap.timedelta(days=7)
    entres = {i["id"] for i, _ in recap.entres_depuis(seuil)}
    for uid in uids:
        assert uid in entres, f"{uid} absent du récap 7 jours"
    ligne = recap.ligne(store.lire(uids[0]))
    assert "Camille MARTIN" in ligne and "début" in ligne


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()

    # sans session, toute action RH est fermée
    assert c.get("/suivi").status_code == 302
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "wingstop-rh"})
    assert c.get("/suivi").status_code == 200

    dk = couloir_dark_kitchen(c)
    resto = couloir_restaurant(c)
    fiche_absente_si_non_declaree(c)
    recap_liste_la_semaine([dk, resto])

    # lien forgé
    anonyme = app.test_client()
    assert anonyme.get("/lot/lot_comptable.X.1.0.deadbeef").status_code == 410

    print(f"\nDeux couloirs OK — Dark Kitchen {dk}, Restaurant {resto}\n{TEMP}")


if __name__ == "__main__":
    main()
