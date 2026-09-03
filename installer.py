"""Crée l'instance d'un nouveau client, hors du dépôt.

    python installer.py "ACME Restauration" /srv/acme

Le code est le produit ; l'instance est un dossier à part, qui ne contient que
ce qui appartient au client :

    /srv/acme/
      config/     <- copie de config.exemple/, secrets générés, à compléter
      data/       <- soumissions, dossiers, pièces (la sauvegarde, c'est ça)

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


def main(nom, cible):
    cible = Path(cible)
    config = cible / "config"
    if config.exists():
        sys.exit(f"{config} existe déjà — refus d'écraser une instance installée.")

    shutil.copytree(SQUELETTE, config)
    (cible / "data").mkdir(parents=True, exist_ok=True)

    mdp = secrets.token_urlsafe(9)
    conf = json.loads((config / "instance.json").read_text(encoding="utf-8"))
    conf["client"] = nom
    conf["secret"] = secrets.token_hex(32)
    conf["utilisateurs"] = {"rh": {"mdp_hash": generate_password_hash(mdp),
                                   "admin": True}}
    (config / "instance.json").write_text(
        json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")

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

Puis :
  CONFIG_DIR={config} DONNEES={cible}/data python app.py
""")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
