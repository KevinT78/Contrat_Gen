"""Prepare la base neuve de la preprod a partir de la prod, sans aucune donnee
personnelle : seules les societes (SIRET, mentions legales) sont copiees, et
un compte rh neuf est cree. Les dossiers, pieces et journaux restent en prod.

    python deploy/init_preprod.py /etc/contratgen/wingstop.env /etc/contratgen/preprod.env

La prod n'est que lue. Refuse si les deux fichiers visent la meme base, ou si
la base de preprod a deja un compte rh (elle sert deja).
"""
import secrets
import sys

from pymongo import MongoClient
from werkzeug.security import generate_password_hash

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def lire_env(chemin):
    """Meme lecture que config.py : CLE=valeur, coupee au premier '='."""
    env = {}
    for ligne in open(chemin, encoding="utf-8"):
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#") and "=" in ligne:
            cle, valeur = ligne.split("=", 1)
            env[cle.strip()] = valeur.strip().strip("'\"")
    return env


def base(env):
    return MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=8000)[env["MONGO_DB"]]


def main(env_prod, env_preprod):
    prod, preprod = lire_env(env_prod), lire_env(env_preprod)
    if prod["MONGO_DB"] == preprod["MONGO_DB"]:
        sys.exit(f"les deux fichiers visent la même base « {prod['MONGO_DB']} » — refus.")
    source, cible = base(prod), base(preprod)
    if cible.comptes.find_one({"_id": "rh"}):
        sys.exit(f"la base « {preprod['MONGO_DB']} » a déjà un compte rh : elle sert déjà — refus.")

    societes = list(source.societes.find())
    if societes:
        cible.societes.insert_many(societes)
    mdp = secrets.token_urlsafe(9)
    cible.comptes.replace_one({"_id": "rh"}, {"mdp_hash": generate_password_hash(mdp)}, upsert=True)

    print(f"Base « {preprod['MONGO_DB']} » prête : {len(societes)} société(s) copiée(s) depuis "
          f"« {prod['MONGO_DB']} », aucun dossier ni pièce.")
    print(f"Compte preprod : rh / {mdp}      <- noté une seule fois, ici")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
