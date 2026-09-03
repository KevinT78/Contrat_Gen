"""Parcours RH complet servi sur la vraie config Wingstop (config_wingstop/).

    python tests/test_parcours_wingstop.py

Formulaire réel (30 questions, NOM+Prénom en un champ, e-mail, dates) -> suivi
-> validation -> saisie RH du planning -> contrat .html généré -> signé -> DPAE
-> remise au comptable. Prouve que l'app tourne sur une config où les ids de
champs ne matchent plus ceux codés en dur (indirection de rôle) et où le
contrat n'est pas un .docx.

Écrit dans un dossier temporaire, ne touche jamais data/.
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
RACINE = Path(__file__).resolve().parent.parent
os.environ["CONFIG_DIR"] = str(RACINE / "config_wingstop")
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="parcours-wingstop-")
sys.path.insert(0, str(RACINE))

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions", "documents"):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF", "p.pdf")


def piece():
    return (BytesIO(PDF[0]), PDF[1])


def saisie(poste="Manager", **over):
    c = {
        "etablissement": "Wing Kitchen / Boulogne (DK)",
        "poste": poste, "civilite": "Madame",
        "nom_prenom": "NKEMBA Awa", "date_naissance": "1996-03-07",
        "telephone": "0612345678", "email": "manager.boulogne@example.com",
        "adresse": "9 rue des Lilas, 92100 Boulogne-Billancourt",
        "num_secu": "2 96 03 92 042 123 45",
        "date_embauche": "2026-10-01", "type_contrat": "CDI",
        "date_debut": "2026-10-01", "heure_demarrage": "9h30",
        "nationalite": "Français", "nationalite_etrangere": "",
        "type_autorisation": "", "date_fin_validite": "",
        "temps_partiel": "NON", "temps_travail": "35H",
        "lundi": "", "mardi": "", "mercredi": "", "jeudi": "",
        "vendredi": "", "samedi": "", "dimanche": "",
    }
    c.update(over)
    return c


def dernier_mail(nom):
    fichiers = sorted((config.DONNEES / "mails").glob(f"*-{nom}.eml"))
    assert fichiers, f"aucun mail « {nom} » envoyé"
    msg = email.message_from_bytes(fichiers[-1].read_bytes())
    return msg["To"] + "\n" + msg.get_payload(decode=True).decode("utf-8")


def soumettre(c, s):
    r = c.post("/", data={**s, "carte_vitale": piece(), "rib": piece(),
                          "cni": piece(), "justif_domicile": piece()},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())


def parcours_manager(c):
    uid = soumettre(c, saisie(poste="Manager"))

    # le mail RH porte le nom via le rôle (champ « nom_prenom »), pas « nom_naissance »
    assert "NKEMBA Awa" in dernier_mail("nouvelle_soumission")

    # KO puis correction par lien signé
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante", "commentaire": "RIB illisible"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    ko = dernier_mail("rejet")
    assert "manager.boulogne@example.com" in ko, "email demandeur absent du KO"
    lien = re.search(r"http://localhost/corriger/[\w.=-]+", ko).group(0)
    r = c.post(lien, data={**saisie(poste="Manager"), "rib": piece()},
               content_type="multipart/form-data")
    assert "Correction envoyée" in r.text
    assert c.get(lien).status_code == 410

    # validation -> dossier ; pas de fiche salarié (non déclarée pour Wingstop)
    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert item["_zone"] == "documents" and store.etat(item) == "ATraiter"

    # l'écran dossier expose les champs de saisie RH
    page = c.get(f"/dossier/{uid}").text
    assert 'name="ReposConsecutifs"' in page, "champ de saisie RH absent de l'écran"

    # génération : la RH renseigne le planning ; le salaire vient de la grille
    c.post(f"/dossier/{uid}/contrat",
           data={"Semaine1": "35", "Semaine2": "35", "Semaine3": "35", "Semaine4": "35",
                 "ReposConsecutifs": "Oui", "ReposFractionnes": "Non"})
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert item["journal"][-1]["modele"] == "Manager.html"
    produits = store.fichiers(item, "contrat")
    assert "contrat.html" in produits, produits
    txt = (Path(item["_dir"]) / "contrat" / "contrat.html").read_text(encoding="utf-8")
    assert "{{" not in txt, "contrat troué"
    for attendu in ("Madame Awa NKEMBA", "1er octobre 2026",
                    "2 500 (deux mille cinq cents) euros bruts",
                    "Wing Kitchen Boulogne"):
        assert attendu in txt, f"« {attendu} » absent du contrat"
    # la saisie RH est persistée dans le dossier
    assert store.lire(uid)["champs"]["ReposConsecutifs"] == "Oui"

    # signature manuelle -> DPAE -> remise
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne"
    c.post(f"/dossier/{uid}/rappel-dpae")
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"

    mail = dernier_mail("avis_comptable")
    assert "compta@wingflavors.example" in mail
    lot = re.search(r"http://localhost/lot/[\w.=-]+", mail).group(0)
    z = zipfile.ZipFile(BytesIO(app.test_client().get(lot + "/zip").data))
    assert "contrat/contrat.html" in z.namelist(), z.namelist()
    assert "contrat/contrat-signe.pdf" in z.namelist()
    return uid


def parcours_equipier_partiel_etranger(c):
    """Deuxième couloir : temps partiel (bascule de template) + salarié étranger
    (bloc titre de séjour) + salaire barématisé."""
    uid = soumettre(c, saisie(
        poste="Equipier Polyvalent", civilite="Monsieur", nom_prenom="DUPONT Jean",
        temps_partiel="OUI", temps_travail="24H",
        nationalite="Autres", nationalite_etrangere="Ivoirienne",
        type_autorisation="Carte de séjour", date_fin_validite="2027-08-31",
        lundi="9h-15h", mardi="9h-15h", jeudi="9h-15h", vendredi="9h-15h"))
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat",
           data={"Semaine1": "24", "Semaine2": "24", "Semaine3": "24", "Semaine4": "24",
                 "ReposConsecutifs": "Oui", "ReposFractionnes": "Non"})
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert item["journal"][-1]["modele"] == "Equipier_Partiel.html", item["journal"][-1]
    txt = (Path(item["_dir"]) / "contrat" / "contrat.html").read_text(encoding="utf-8")
    assert "{{" not in txt
    for attendu in ("Monsieur Jean DUPONT",
                    "104 heures par mois",                       # 24 h/sem mensualisé
                    "1 280,24 (mille deux cent quatre-vingts euros et vingt-quatre centimes)",
                    "titre de séjour de type « Carte de séjour »",
                    "valable jusqu'au 31 août 2027",
                    "<td>9h-15h</td>"):
        assert attendu in txt, f"« {attendu} » absent du contrat partiel"
    return uid


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()
    assert c.get("/suivi").status_code == 302
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "wingstop-rh"})
    assert c.get("/suivi").status_code == 200

    m = parcours_manager(c)
    e = parcours_equipier_partiel_etranger(c)
    print(f"\nParcours Wingstop OK — Manager {m}, Équipier partiel étranger {e}"
          f"\n{config.DONNEES}")


if __name__ == "__main__":
    main()
