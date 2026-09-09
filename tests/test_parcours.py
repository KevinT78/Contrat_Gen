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

for zone in ("soumissions",):
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


def piece_refusee(_c):
    """Formulaire public : une pièce hors liste blanche (.docx) est un message,
    pas un 500, et ne laisse aucune soumission à moitié née sur le disque.
    Client dédié : l'erreur met les bonnes pièces de côté (brouillon), rien ne
    doit fuiter dans la session d'un appelant qui réutilise son client."""
    c = app.test_client()
    avant = {d.name for d in (config.DONNEES / "soumissions").glob("*/")}
    r = c.post("/", data={**base_saisie("Wingstop France / Lille Grand Place"),
                          "identite": piece(), "carte_vitale": piece(),
                          "rib": (BytesIO(b"PK"), "rib.docx")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "format .docx refusé" in r.text, \
        (r.status_code, r.text[:300])
    apres = {d.name for d in (config.DONNEES / "soumissions").glob("*/")}
    assert apres == avant, f"soumission orpheline créée : {apres - avant}"


def couloir_dark_kitchen(c):
    """Lille Grand Place — contrat généré par l'app, signature manuelle."""
    piece_refusee(c)
    uid = soumettre(c, base_saisie("Wingstop France / Lille Grand Place"))

    # KO puis correction
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante", "commentaire": "RIB flou"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    ko = dernier_mail("rejet")
    assert ko.startswith("manager@example.com\n"), \
        "établissement sans manager_email : le rejet doit retomber sur l'email saisi"
    lien = lien_dans(ko, "corriger")
    r = c.post(lien, data={**base_saisie("Wingstop France / Lille Grand Place"),
                           "rib": piece()}, content_type="multipart/form-data")
    assert "Correction envoyée" in r.text
    assert c.get(lien).status_code == 410, "lien de correction encore vivant"
    assert (Path(store.lire(uid)["_dir"]) / "_versions").is_dir(), "re-dépôt non archivé"

    # validation -> dossier + fiche salarié + contrat généré dans la foulée
    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert item["_zone"] == "documents" and store.etat(item) == "ContratPret"
    assert Path(item["_dir"]).relative_to(config.DONNEES).as_posix() == \
        "DOSSIERS SALARIES/DARK KITCHENS/Lille Grand Place/Manager/MARTIN Camille", item["_dir"]
    assert store.chemin(uid, "pieces").name == "FICHE PERSONNELLE"
    assert store.chemin(uid, "contrat").name == "CONTRAT"
    store._carte.clear()                      # uid -> chemin par le seul disque
    assert store.lire(uid)["_dir"] == item["_dir"], "scan de resolution KO"
    assert "fiche-salarie.docx" in store.fichiers(item, "pieces"), \
        "fiche salarié absente de FICHE PERSONNELLE/ après validation"
    assert any(e.get("type") == "fiche_salarie" for e in item["journal"])
    # le rappel DPAE est un EFFET de la validation (schéma, étape 6) : asserté
    # ici, au niveau de l'appelant, avec les informations DPAE et le SIRET
    rappel = dernier_mail("rappel_dpae")
    for attendu in ("Martin", "01/10/2026", "884 512 336 00027",
                    "Lille Grand Place", "http://localhost/dossier/"):
        assert attendu in rappel, f"« {attendu} » absent du rappel DPAE :\n{rappel}"

    # refus : RemisComptable direct (garde de store.TRANSITIONS)
    try:
        store.transition(uid, "RemisComptable", "test")
        assert False, "transition ContratPret -> RemisComptable acceptée"
    except ValueError:
        pass

    # contrat produit à la validation, sans étape « Générer » séparée
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert item["journal"][-1]["modele"] == "CDI_Manager.docx"
    from docx import Document
    import contrat as moteur
    doc = Document(str(store.chemin(item["id"], "contrat", "contrat.docx")))
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

    # DPAE faite (accusé obligatoire)
    c.post(f"/dossier/{uid}/dpae-faite", data={}, content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne", "DPAE validée sans accusé"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"

    # remise : plus de mail par dossier -- copie dans data/compta/, lien dans
    # le mail hebdo. Le lot porte contrat, contrat signé, fiche salarié, accusé.
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    assert not list((config.DONNEES / "mails").glob("*-avis_comptable.eml")), \
        "un mail par dossier est encore parti à la remise"
    copie = config.DONNEES / "compta" / "Wingstop France" / f"Camille MARTIN - {uid}"
    assert sorted(p.relative_to(copie).as_posix() for p in copie.rglob("*") if p.is_file()) == [
        "contrat/accuse-dpae.pdf", "contrat/contrat-signe.pdf", "contrat/contrat.docx",
        "pieces/carte-vitale.pdf", "pieces/fiche-salarie.docx",
        "pieces/identite.pdf", "pieces/rib.pdf"], "copie compta incomplète"
    lot = "http://localhost/lot/" + store.signer("lot_comptable", uid,
                                                 item.get("lien_comptable_epoch", 0))
    anonyme = app.test_client()
    z = zipfile.ZipFile(BytesIO(anonyme.get(lot + "/zip").data))
    assert sorted(z.namelist()) == [
        "contrat/accuse-dpae.pdf", "contrat/contrat-signe.pdf", "contrat/contrat.docx",
        "pieces/carte-vitale.pdf", "pieces/fiche-salarie.docx",
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
    assert (config.DONNEES / "compta" / "Wingstop Sud").is_dir()
    return uid


def fiche_absente_si_non_declaree(c):
    """Sans "fiche_salarie" dans instance.json, aucune fiche n'est produite."""
    garde = config.instance().pop("fiche_salarie", None)
    try:
        uid = soumettre(c, base_saisie("Wingstop France / Paris Opéra"))
        c.post(f"/dossier/{uid}/valider")
        assert "fiche-salarie.docx" not in store.fichiers(store.lire(uid), "pieces")
    finally:
        if garde:
            config.instance()["fiche_salarie"] = garde


def rejet_part_au_manager_de_l_etablissement(c):
    """Paris Opéra déclare manager_email : le rejet y part, pas à l'email saisi."""
    uid = soumettre(c, base_saisie("Wingstop France / Paris Opéra"))
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Autre", "commentaire": "test adresse fixe"})
    ko = dernier_mail("rejet")
    assert ko.startswith("opera@example.com\n"), ko.splitlines()[0]


def recap_liste_la_semaine(uids):
    """Un mail PAR CABINET, RH en copie, un lien de lot vivant par dossier remis."""
    import recap
    seuil = recap.datetime.now(recap.timezone.utc) - recap.timedelta(days=7)
    remis = {i["id"] for i in recap.remis_depuis(seuil)}
    for uid in uids:
        assert uid in remis, f"{uid} absent du récap 7 jours"
    try:
        recap.main(7)
    except SystemExit as e:
        assert e.code == 0, "un mail hebdo n'est pas parti"
    fichiers = sorted((config.DONNEES / "mails").glob("*-recap_hebdo.eml"))
    assert len(fichiers) == 2, f"{len(fichiers)} mails hebdo pour 2 cabinets"
    par_cabinet = {}
    for f in fichiers:
        msg = email.message_from_bytes(f.read_bytes())
        assert msg["Cc"] == "rh@example.com", msg["Cc"]
        par_cabinet[msg["To"]] = msg.get_payload(decode=True).decode("utf-8")
    nord, sud = par_cabinet["cabinet-nord@example.com"], par_cabinet["cabinet-sud@example.com"]
    assert uids[0] in nord and uids[1] not in nord, "un cabinet voit l'autre société"
    assert uids[1] in sud and uids[0] not in sud
    assert "Camille MARTIN" in nord and "début" in nord
    lot = lien_dans(nord, "lot")
    assert app.test_client().get(lot).status_code == 200, "lien du mail hebdo mort"


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()

    # sans session, toute action RH est fermée
    assert c.get("/suivi").status_code == 302
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    assert c.get("/suivi").status_code == 200

    dk = couloir_dark_kitchen(c)
    resto = couloir_restaurant(c)
    fiche_absente_si_non_declaree(c)
    rejet_part_au_manager_de_l_etablissement(c)
    recap_liste_la_semaine([dk, resto])

    # lien forgé
    anonyme = app.test_client()
    assert anonyme.get("/lot/lot_comptable.X.1.0.deadbeef").status_code == 410

    print(f"\nDeux couloirs OK — Dark Kitchen {dk}, Restaurant {resto}\n{TEMP}")


if __name__ == "__main__":
    main()
