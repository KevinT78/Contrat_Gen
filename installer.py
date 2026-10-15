"""Crée l'instance d'un nouveau client, hors du dépôt.

    python installer.py "ACME Restauration" /srv/acme

À lancer avec MONGO_URI et MONGO_DB dans l'environnement : la base neuve de ce
client, qui reçoit le premier compte. L'instance est un dossier à part, qui ne
contient que ce qui appartient au client :

    /srv/acme/
      config/     <- copie de config.exemple/, à compléter
      .env        <- secrets (SECRET_KEY généré, MONGO_URI, MONGO_DB, CONFIG_DIR)

Les données (dossiers, pièces, comptes, sociétés) vivent en base, jamais sur le
disque. Les secrets vivent dans .env, jamais dans config/ : config/ se copie et
se montre au client.

L'installateur **copie** le squelette au lieu de nettoyer une config existante :
aucune valeur d'un autre client ne peut survivre par oubli. Il refuse d'écrire
sur un config/ ou un .env déjà présent — une réinstallation par-dessus une
instance qui tourne effacerait ses secrets.
"""
import json
import os
import secrets
import shutil
import sys
from pathlib import Path


sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")   # le refus d'écraser part sur stderr
SQUELETTE = Path(__file__).parent / "config.exemple"


def main(nom, cible):
    cible = Path(cible).resolve()        # systemd (EnvironmentFile) exige un chemin absolu
    config, env = cible / "config", cible / ".env"
    for f in (config, env):
        if f.exists():
            sys.exit(f"{f} existe déjà — refus d'écraser une instance installée.")

    # Le compte d'abord : sans base joignable (MONGO_URI/MONGO_DB), rien n'est
    # cree et l'installateur dit quoi poser, au lieu d'une instance sans compte.
    import store              # apres les refus ci-dessus : importe config
    mdp = secrets.token_urlsafe(9)
    try:
        # Une base = une instance : un compte rh deja la, c'est la base d'un autre
        # client (MONGO_DB oublie dans le shell), dont on ecraserait le mot de passe.
        if store.db().comptes.find_one({"_id": "rh"}):
            sys.exit(f"la base « {os.environ['MONGO_DB']} » a déjà un compte rh : elle "
                     "sert une autre instance. Donnez une base neuve (MONGO_DB).")
        store.poser_compte("rh", mdp)
    except RuntimeError as e:
        sys.exit(str(e))

    shutil.copytree(SQUELETTE, config)
    conf = json.loads((config / "instance.json").read_text(encoding="utf-8"))
    conf["client"] = nom
    (config / "instance.json").write_text(
        json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")

    # Une ligne CLE=valeur, sans commentaire ni guillemets : lu tel quel par
    # systemd (EnvironmentFile) comme par la ligne PowerShell affichee plus bas.
    valeurs = {"SECRET_KEY": secrets.token_hex(32), "MONGO_URI": os.environ["MONGO_URI"],
               "MONGO_DB": os.environ["MONGO_DB"], "CONFIG_DIR": str(config)}
    env.write_text("".join(f"{k}={v}\n" for k, v in valeurs.items()), encoding="utf-8")
    env.chmod(0o600)          # sans effet sous Windows : le profil utilisateur protege deja

    e = f'--env "{env}"'
    print(f"""
Instance « {nom} » créée dans {cible}

  Compte RH : rh / {mdp}      <- noté une seule fois, ici
  Secrets   : {env}           <- SECRET_KEY, MONGO_URI, MONGO_DB, CONFIG_DIR

Chaque commande ci-dessous prend ce fichier avec  {e}
  VPS : ExecStart=…/python app.py {e} dans l'unité systemd, fichier lisible
        par le compte du service seulement (chmod 600)
Relais SMTP authentifié : ajoutez SMTP_MOT_DE_PASSE=… au fichier ; Yousign : YOUSIGN_CLE_API=…

À compléter avant de servir (le démarrage refuse tant que ce n'est pas fait) :
  1. sociétés et établissements (SIRET, mentions légales, cabinet comptable), en base :
     python configurer.py --assister {e}
     ou, depuis un fichier : python configurer.py importer-societes <fichier.json> {e}
  2. {config / "instance.json"}  — bloc "mails" (SMTP + destinataires), puis "mode": "smtp"
  3. {config / "formulaire.json"} — les champs propres au client, et leur placeholder
  4. python placeholders.py {e}  — génère la fiche des {{{{Jetons}}}} à envoyer au client,
     qui balise lui-même ses .docx et les renvoie
  5. déposer les .docx reçus dans {config / "contrats"}, puis les déclarer
     dans "templates" (poste -> fichier)

À tout moment :
  python configurer.py {e}             <- bilan : ce qui manque, et où
  python configurer.py --assister {e}  <- guidé, question par question

Puis : python app.py {e}
""")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
