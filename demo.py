"""Démo de bout en bout sur la config de démo (config.demo/).

    python demo.py            # remet la démo à zéro, amorce 4 dossiers, sert sur :5000
    python demo.py --vide     # suivi vide : c'est toi qui fais tout, du formulaire
                              # public à la remise au cabinet (pièces imprimées)

Données dans data_demo/ (jamais data/), effacées à chaque lancement. Mails en
mode console : data_demo/mails/*.eml. Identifiants RH : rh / demo-rh.

Quatre dossiers amorcés pour que le suivi ne soit pas vide au moment de
présenter, chacun arrêté à une étape différente :
  - BENALI Sarah   : demande reçue, à valider — parcours « contrat généré » à jouer en live
  - SOARES Rui     : demande validée, établissement en couloir myrhis — « contrat déposé »
                     à jouer en live : « Déposer le contrat » au lieu de « Générer »
  - NKEMBA Awa     : contrat généré, DPAE à faire, badge « signé manquant »
  - DUPONT Jean    : remis au cabinet (montre le lien comptable + mail hebdo)
"""
import contextlib
import io
import os
import shutil
import sys
from io import BytesIO
from pathlib import Path

RACINE = Path(__file__).resolve().parent
os.environ.setdefault("CONFIG_DIR", str(RACINE / "config.demo"))
os.environ.setdefault("DONNEES", str(RACINE / "data_demo"))
os.environ.setdefault("PORT", "5000")
sys.stdout.reconfigure(encoding="utf-8")
# stderr AUSSI : le refus ci-dessous part par sys.exit(), donc sur stderr, et
# sortirait en cp1252 illisible des que la sortie n'est pas un vrai terminal.
sys.stderr.reconfigure(encoding="utf-8")

# AVANT le rmtree : la demo tourne sur la config du client, qui ne vit pas dans
# le depot. Sur un clone frais elle est absente -- le dire, plutot que d'effacer
# data_demo/ puis d'exploser sur un FileNotFoundError d'instance.json.
if not (Path(os.environ["CONFIG_DIR"]) / "instance.json").exists():
    sys.exit(f"Config introuvable : {os.environ['CONFIG_DIR']}\n"
             "La démo nécessite une config complète (formulaire riche, "
             "modèles de contrats, grille de salaires), pas une instance vide.")

shutil.rmtree(os.environ["DONNEES"], ignore_errors=True)
for zone in ("soumissions", "mails"):
    (Path(os.environ["DONNEES"]) / zone).mkdir(parents=True)

import config  # noqa: E402
import store   # noqa: E402
import app as serveur  # noqa: E402

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


# Les etablissements du client sont nommes dans config/, pas ici : ce
# fichier est versionne, la config non. On les choisit par ce que la demo
# doit montrer -- un couloir « contrat genere », un couloir « depose ».
GENERE = [c for c, _ in config.etablissements() if config.mode_contrat(c) == "genere"]
DEPOSE = next((c for c, _ in config.etablissements()
               if config.mode_contrat(c) == "depose"), None)
if not GENERE:
    sys.exit("aucun établissement en contrat « genere » dans societes.json")


def piece():
    return (BytesIO(PDF), "piece.pdf")


def saisie(**over):
    c = {
        "etablissement": GENERE[0], "poste": "Equipier Polyvalent",
        "civilite": "Madame", "nom_prenom": "BENALI Sarah", "date_naissance": "2001-02-11",
        "telephone": "0612345678", "email": "sarah.benali@example.com",
        "adresse": "4 rue Gallieni, 92100 Villeneuve",
        "num_secu": "2 01 02 92 012 345 67",
        "date_embauche": "2026-10-12", "type_contrat": "CDI", "date_debut": "2026-10-12",
        "heure_demarrage": "10h00", "nationalite": "Français", "nationalite_etrangere": "",
        "type_autorisation": "", "date_fin_validite": "",
        "temps_partiel": "NON", "temps_travail": "35H",
        "lundi": "", "mardi": "", "mercredi": "", "jeudi": "",
        "vendredi": "", "samedi": "", "dimanche": "",
    }
    c.update(over)
    return c


def soumettre(c, s):
    r = c.post("/", data={**s, "carte_vitale": piece(), "rib": piece(),
                          "cni": [piece(), piece()],  # recto + verso
                          "justif_domicile": piece()},
               content_type="multipart/form-data")
    assert "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())


def amorcer():
    c = serveur.app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "demo-rh"})

    # 1. remis au comptable : équipier partiel étranger, tout le parcours
    uid = soumettre(c, saisie(
        civilite="Monsieur", nom_prenom="DUPONT Jean", date_naissance="1999-07-23",
        email="jean.dupont@example.com", etablissement=GENERE[1 % len(GENERE)],
        date_embauche="2026-09-28", date_debut="2026-09-28",
        temps_partiel="OUI", temps_travail="24H",
        nationalite="Autres", nationalite_etrangere="Ivoirienne",
        type_autorisation="Carte de séjour", date_fin_validite="2027-08-31",
        lundi="11h-15h / 18h-23h", jeudi="11h-15h / 18h-23h", samedi="12h-23h"))
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat")
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{uid}/remettre")
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "RemisComptable"

    # 2. contrat prêt : manager, DPAE à faire, contrat signé pas encore reçu
    uid = soumettre(c, saisie(
        poste="Manager", nom_prenom="NKEMBA Awa", date_naissance="1996-03-07",
        email="awa.nkemba@example.com", etablissement=GENERE[2 % len(GENERE)],
        date_embauche="2026-10-01", date_debut="2026-10-01", heure_demarrage="9h30"))
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat")
    assert store.etat(store.lire(uid)) == "ContratPret"

    # 3. demande reçue : à valider en live (parcours « contrat généré »)
    uid = soumettre(c, saisie())
    assert store.etat(store.lire(uid)) == "Soumise"

    # 4. couloir « contrat déposé » : l etablissement est en myrhis (societes.json), le
    #    contrat est fait à la main hors app. Validé, en attente du dépôt — à
    #    jouer en live : l'écran propose « Déposer le contrat », pas « Générer ».
    uid = soumettre(c, saisie(
        civilite="Monsieur", nom_prenom="SOARES Rui", date_naissance="1998-05-14",
        email="rui.soares@example.com", etablissement=DEPOSE,
        date_embauche="2026-10-19", date_debut="2026-10-19"))
    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ATraiter"


FICHE = """\
Suivi vide : à toi de jouer, du formulaire public à la remise au cabinet.
  1. /  -> remplis la fiche ci-dessous, joins les pièces -> « Envoyer la demande »
       (CNI : sélectionne les DEUX fichiers cni_recto.pdf + cni_verso.pdf)
  2. /login (rh / demo-rh) -> « Demandes d'embauche » -> clic sur BENALI Sarah
  3. « Accepter — ouvrir le dossier »
        -> mail « rappel DPAE » dans data_demo/mails/
  4. « Générer le contrat » -> aucun champ à saisir -> ouvre contrat.docx
  5. « DPAE créée et stockée » -> dépose l'accusé (un des PDF)  <- la DPAE dans le dossier
  6. « Remettre au cabinet comptable » -> copie data_demo/compta/ + lien de lot
  7. Salariés -> fiche du salarié -> « Déposer le contrat signé » (en haut) -> un des PDF

Fiche candidat (parcours « contrat généré ») :
  Établissement ......... {etab}
  Poste ................ Equipier Polyvalent
  Civilité / NOM Prénom  Madame / BENALI Sarah
  Naissance ............ 11/02/2001
  Téléphone / Email .... 0612345678 / sarah.benali@example.com
  Adresse ............. 4 rue Gallieni, 92100 Villeneuve
  N° sécu ............ 2 01 02 92 012 345 67
  Embauche / Début ... 12/10/2026 / 12/10/2026     Heure démarrage ... 10h00
  Type contrat ....... CDI    Nationalité ... Français
  Temps partiel ...... NON    Temps travail . 35H     Dispos ... laisser vide
  Pièces jointes ..... {dossier}
       (carte_vitale.pdf, rib.pdf, cni_recto.pdf + cni_verso.pdf, justif_domicile.pdf)

Variante « contrat déposé » : même fiche, Établissement = {etab_depose}
  -> l'écran RH propose « Déposer le contrat » (pas « Générer ») ; l'étape 4
     devient un simple upload du PDF fait à la main, la suite est identique.
"""

if __name__ == "__main__":
    fiche = ""
    if "--vide" in sys.argv:
        pieces = config.DONNEES / "pieces_demo"
        pieces.mkdir(exist_ok=True)
        for nom in ("carte_vitale", "rib", "cni_recto", "cni_verso", "justif_domicile"):
            (pieces / f"{nom}.pdf").write_bytes(PDF)
        fiche = "\n" + FICHE.format(dossier=pieces, etab=GENERE[0],
                                    etab_depose=DEPOSE or "aucun en config")
    else:
        # les mails « console » de l'amorçage n'ont rien à faire sur l'écran de démo
        with contextlib.redirect_stdout(io.StringIO()):
            amorcer()
    print(f"""
Démo Contrat_Gen — {config.instance()['client']}
  Formulaire (public) : http://localhost:{os.environ['PORT']}/
  Espace RH           : http://localhost:{os.environ['PORT']}/login   (rh / demo-rh)
  Mails « envoyés »   : {config.DONNEES / 'mails'}
  Mail hebdo cabinet  : CONFIG_DIR=config.demo DONNEES=data_demo python recap.py
{fiche}""", flush=True)
    sys.argv = [sys.argv[0]]
    serveur.demarrer()
