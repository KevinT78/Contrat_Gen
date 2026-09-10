"""Crée l'instance d'un nouveau client, hors du dépôt.

    python installer.py "ACME Restauration" /srv/acme
    python installer.py "ACME Restauration" /srv/acme "C:/Users/rh/OneDrive - ACME/Embauches"

Le code est le produit ; l'instance est un dossier à part, qui ne contient que
ce qui appartient au client :

    /srv/acme/
      config/     <- copie de config.exemple/, secrets générés, à compléter
      data/       <- soumissions, dossiers, pièces (la sauvegarde, c'est ça)

Le 3e argument, optionnel, met `data/` ailleurs : un dossier répliqué par le
client de synchronisation d'un drive (OneDrive, SharePoint, Google Drive) déjà
installé sur cette machine. L'app y écrit comme sur un disque local, le choix
est écrit dans instance.json (bloc `stockage`) et vérifié au démarrage. Sans
3e argument : `data/` dans l'instance, comme avant.

L'installateur **copie** le squelette au lieu de nettoyer une config existante :
aucune valeur d'un autre client ne peut survivre par oubli. Il refuse d'écrire
sur un config/ déjà présent — une réinstallation par-dessus une instance qui
tourne effacerait ses secrets et ses comptes.
"""
import json
import secrets
import shutil
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")   # le refus d'écraser part sur stderr
SQUELETTE = Path(__file__).parent / "config.exemple"


def main(nom, cible, drive=None):
    cible = Path(cible)
    config = cible / "config"
    if config.exists():
        sys.exit(f"{config} existe déjà — refus d'écraser une instance installée.")

    if drive:
        drive = Path(drive)
        # Creer la racine du drive elle-meme serait creer un dossier NON
        # synchronise qui ressemble a un dossier synchronise : on cree le
        # sous-dossier, jamais son parent.
        if not drive.parent.is_dir():
            sys.exit(f"{drive.parent} n'existe pas — donnez un chemin situé dans le "
                     f"dossier répliqué par le client de synchronisation.")
        drive.mkdir(exist_ok=True)
        # Meme refus que sur config/ : deux instances sur le meme dossier drive
        # fusionneraient leurs dossiers salaries, et `store._verrou` ne protege
        # qu'a l'interieur d'un process -- entrees de journal perdues.
        if any(drive.iterdir()):
            sys.exit(f"{drive} n'est pas vide — donnez un dossier neuf : "
                     f"deux instances sur le même dossier perdraient des données.")

    shutil.copytree(SQUELETTE, config)
    if not drive:
        (cible / "data").mkdir(parents=True, exist_ok=True)

    mdp = secrets.token_urlsafe(9)
    conf = json.loads((config / "instance.json").read_text(encoding="utf-8"))
    conf["client"] = nom
    conf["stockage"] = ({"mode": "dossier", "chemin": str(drive)} if drive
                        else {"mode": "local"})
    conf["secret"] = secrets.token_hex(32)
    conf["utilisateurs"] = {"rh": {"mdp_hash": generate_password_hash(mdp)}}
    (config / "instance.json").write_text(
        json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")

    ou = f"{drive} (dossier synchronisé)" if drive else f"{cible / 'data'}"
    print(f"""
Instance « {nom} » créée dans {cible}

  Compte RH : rh / {mdp}      <- noté une seule fois, ici

À compléter avant de servir (le démarrage refuse tant que ce n'est pas fait) :
  1. {config}/societes.json  — sociétés, SIRET, mentions légales, cabinet comptable ;
     "contrat": "genere" (contrat produit par l'app) ou "depose" (fait à la main)
  2. {config}/instance.json  — bloc "mails" (SMTP + destinataires), puis "mode": "smtp"
  3. {config}/formulaire.json — les champs propres au client, et leur placeholder
  4. python placeholders.py  — génère la fiche des {{{{Jetons}}}} à envoyer au client,
     qui balise lui-même ses .docx et les renvoie
  5. déposer les .docx reçus dans {config}/contrats/, puis les déclarer
     dans "templates" (poste -> fichier)

À tout moment :
  CONFIG_DIR={config} python configurer.py             <- bilan : ce qui manque, et où
  CONFIG_DIR={config} python configurer.py --assister  <- guidé, question par question

Puis :
  CONFIG_DIR={config} python app.py

Les données vont dans {ou} — l'instance le sait : plus besoin de DONNEES= sur la
ligne de lancement (qui reste prioritaire si vous la posez quand même).
""")


if __name__ == "__main__":
    if not 3 <= len(sys.argv) <= 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
