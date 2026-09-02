"""Installe cette copie du dépôt pour un nouveau client.

    python installer.py "ACME Restauration"

Agit **sur place** dans config/ : génère le secret HMAC et le mot de passe du
compte RH, remet à blanc tout ce qui portait les valeurs du client précédent
(sociétés, SIREN/SIRET, mentions, cabinets comptables, postes → templates),
supprime les .docx de config/contrats/. Ce qui reste est le squelette commun :
formulaire, motifs de KO, textes des mails.

Aucune valeur de l'ancien client ne survit par oubli — c'est pour ça que le
squelette n'est pas un config.exemple/ versionné en double.
"""
import json
import secrets
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

sys.stdout.reconfigure(encoding="utf-8")
CONFIG = Path(__file__).parent / "config"

# societes.json est remis à [] : aucun établissement => le démarrage refuse de
# servir tant que le client n'a pas déclaré au moins une société. La forme
# attendue est dans le README.


def main(nom):
    conf = json.loads((CONFIG / "instance.json").read_text(encoding="utf-8"))
    mdp = secrets.token_urlsafe(9)

    conf["client"] = nom
    conf["secret"] = secrets.token_hex(32)
    conf["signature"] = {"mode": "manuel"}
    conf["templates"] = {}
    conf.pop("fiche_salarie", None)
    conf["utilisateurs"] = {"rh": {"mdp_hash": generate_password_hash(mdp), "admin": True}}
    for cle in ("utilisateur", "expediteur", "superviseur", "comptable_defaut"):
        conf["mails"][cle] = ""
    conf["mails"]["rh"] = []
    conf["mails"]["recap"] = None
    conf["mails"]["mot_de_passe"] = ""

    (CONFIG / "instance.json").write_text(
        json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    (CONFIG / "societes.json").write_text("[]\n", encoding="utf-8")
    for docx in (CONFIG / "contrats").glob("*.docx"):
        docx.unlink()

    print(f"""
config/ installé pour « {nom} ».

  Compte RH : rh / {mdp}      <- noté une seule fois, ici

À compléter avant de servir (le démarrage refuse tant que ce n'est pas fait) :
  1. config/societes.json  — sociétés, SIRET, mentions légales, cabinet comptable ;
     "contrat": "genere" (contrat produit par l'app) ou "depose" (fait sur myrhis)
  2. config/instance.json  — bloc "mails" (SMTP + destinataires), puis "mode": "smtp"
  3. config/contrats/      — déposer les .docx du client (placeholders {{{{Nom}}}})
  4. config/instance.json  — "templates" : chaque poste du formulaire vers son .docx,
     et éventuellement "fiche_salarie": "fiche_salarie.docx"
  5. config/formulaire.json — ajouter/retirer les champs propres au client

Puis :  python app.py
""")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
