"""Aide à la configuration d'une instance : bilan, assistant, comptes.

    python configurer.py                 # bilan : verifier -> placeholders -> doctor
    python configurer.py --assister      # pose les questions pour ce qu'il sait remplir
    python configurer.py compte <ident>  # ajoute ou remplace un compte de connexion

Piloté par CONFIG_DIR comme doctor.py. Le bilan est le garde-fou de démarrage
(config.verifier()) rendu lisible AVANT de lancer le serveur : chaque manque
nomme le fichier à ouvrir. Trois instances de vérification ont buté dessus —
personne ne lançait verifier() avant le parcours, et un print cp1252 faisait
passer ses messages accentués pour un bug d'encodage.

L'assistant ne remplit que ce qui se dicte (adresses, comptes, société et
établissements, secret). Le balisage des modèles, les rôles et la grille
restent un travail d'éditeur : il montre où, puis attend qu'on ait corrigé.
"""
import getpass
import json
import secrets
import sys

from werkzeug.security import generate_password_hash

import config

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
if not sys.stdin.isatty():          # réponses par pipe : UTF-8, pas cp1252
    sys.stdin.reconfigure(encoding="utf-8")

# Où ouvrir pour corriger un sujet de verifier() ; à défaut, c'est un modèle.
FICHIERS = {"secret": "instance.json", "compte RH": "instance.json",
            "comptes": "instance.json", "mails": "instance.json",
            "config_version": "instance.json", "derives →": "instance.json",
            "poste «": "instance.json", "url": "instance.json",
            "établissements": "societes.json",
            "roles": "formulaire.json", "grille": "grille.json"}
REPARABLES = ("secret", "url", "mails", "comptes", "compte RH", "établissements")


def fichier(sujet):
    return next((f for p, f in FICHIERS.items() if sujet.startswith(p)),
                f"contrats/{sujet}")


def _ecrire(nom, fn):
    chemin = config.CLIENT / nom
    doc = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else None
    doc = fn(doc)
    chemin.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    config._CACHE.clear()


def _demander(question):
    try:
        return input(f"  {question} : ").strip()
    except EOFError:
        return ""


def _mot_de_passe(question):
    # getpass sous Windows lit la console, pas stdin : un test piloté par pipe
    # resterait bloqué. Sans TTY, on lit stdin comme input().
    lire = (lambda q: getpass.getpass(f"  {q} : ")) if sys.stdin.isatty() else _demander
    mdp = lire(question)
    if not mdp:
        return ""
    if mdp != lire("Confirmation"):
        print("  Les deux saisies diffèrent — rien n'est écrit.")
        return ""
    return mdp


def afficher(manques):
    for sujet, raisons in manques.items():
        print(f"! {sujet}  [config/{fichier(sujet)}]")
        for r in raisons:
            print(f"    {r}")


def bilan():
    manques = config.verifier()
    if manques:
        afficher(manques)
        print(f"\nKO — {len(manques)} sujet(s) à corriger avant de servir.")
        return 1
    import doctor
    import placeholders
    dest = config.CLIENT / "PLACEHOLDERS.md"
    dest.write_text(placeholders.fiche(), encoding="utf-8")
    print(f"Config valide. {len(config.placeholders_connus())} jetons -> {dest}\n")
    lignes = doctor.examiner()
    print(doctor.rapport(lignes))
    code = doctor.verdict(lignes)
    print("\nOK" if code == 0 else "\nKO — un cas ne produit pas son contrat (voir doctor).")
    return code


def compte(ident):
    mdp = _mot_de_passe(f"Mot de passe de « {ident} »")
    if not mdp:
        return 1
    _ecrire("instance.json", lambda d: {**d, "utilisateurs": {
        **d.get("utilisateurs", {}), ident: {"mdp_hash": generate_password_hash(mdp)}}})
    print(f"Compte « {ident} » écrit dans config/instance.json.")
    return 0


# --- assistant : une fonction par sujet réparable -------------------------

def _reparer_secret(_raisons):
    _ecrire("instance.json", lambda d: {**d, "secret": secrets.token_hex(32)})
    print("  secret généré.")


def _reparer_url(_raisons):
    if url := _demander("Adresse publique de l'app (ex. https://embauche.acme.fr)"):
        _ecrire("instance.json", lambda d: {**d, "url": url})


def _reparer_mails(raisons):
    m = dict(config.instance().get("mails") or {})
    if any("rh" in r for r in raisons):
        if rh := _demander("Adresses RH qui reçoivent les demandes (virgules)"):
            m["rh"] = [a.strip() for a in rh.split(",") if a.strip()]
    if any("expediteur" in r for r in raisons):
        if exp := _demander("Adresse d'envoi (expediteur)"):
            m["expediteur"] = exp
    _ecrire("instance.json", lambda d: {**d, "mails": m})


def _reparer_comptes(_raisons):
    if ident := _demander("Identifiant du premier compte (ex. rh)"):
        compte(ident)


def _reparer_compte_rh(_raisons):
    from werkzeug.security import check_password_hash
    for ident, u in config.comptes().items():
        if check_password_hash(u["mdp_hash"], config.MDP_PAR_DEFAUT):
            print(f"  « {ident} » a encore le mot de passe par défaut.")
            compte(ident)


def _reparer_etablissements(_raisons):
    nom = _demander("Nom de la société")
    if not nom:
        return
    soc = {"nom": nom, "siren": _demander("SIREN"),
           "comptable_email": _demander("Email du cabinet comptable"),
           "mentions": {"RaisonSociale": _demander("Raison sociale (mention du contrat)"),
                        "SiegeSocial": _demander("Siège social")},
           "etablissements": []}
    while True:
        e = _demander("Nom de l'établissement")
        if not e:
            break
        etab = {"nom": e, "siret": _demander("SIRET"),
                "manager_email": _demander("Email du manager (reçoit les rejets)"),
                "mentions": {"AdresseEtablissement": _demander("Adresse")}}
        if _demander("Contrat produit par l'app ? [O/n]").lower().startswith("n"):
            etab["contrat"] = "depose"
        soc["etablissements"].append(etab)
        if not _demander("Un autre établissement ? [o/N]").lower().startswith("o"):
            break
    if soc["etablissements"]:
        _ecrire("societes.json", lambda d: (d or []) + [soc])


REPARATIONS = {"secret": _reparer_secret, "url": _reparer_url, "mails": _reparer_mails,
               "comptes": _reparer_comptes, "compte RH": _reparer_compte_rh,
               "établissements": _reparer_etablissements}


def assister():
    while True:
        manques = config.verifier()
        if not manques:
            return bilan()
        afficher(manques)
        print()
        reparables = [s for s in REPARABLES if s in manques]
        for sujet in reparables:
            print(f"-- {sujet}")
            REPARATIONS[sujet](manques[sujet])
        if len(reparables) < len(manques):
            rep = _demander("Corrigez le reste à la main, puis Entrée pour "
                            "revérifier (q pour quitter)")
            if rep.lower() == "q":
                return bilan()
        elif not reparables or config.verifier() == manques:
            # Rien n'a bougé : l'utilisateur a passé toutes les questions.
            return bilan()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(bilan())
    if args == ["--assister"]:
        sys.exit(assister())
    if len(args) == 2 and args[0] == "compte":
        sys.exit(compte(args[1]))
    sys.exit(__doc__)
