"""Aide à la configuration d'une instance : bilan, assistant, comptes.

    python configurer.py                 # bilan : verifier -> placeholders -> doctor
    python configurer.py --assister      # pose les questions pour ce qu'il sait remplir
    python configurer.py compte <ident>  # ajoute ou remplace un compte de connexion
    python configurer.py mail <adresse>  # envoi de test en SMTP, même en mode console

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
import os
import secrets
import sys

from werkzeug.security import generate_password_hash

import config

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
if not sys.stdin.isatty():          # réponses par pipe : UTF-8, pas cp1252
    sys.stdin.reconfigure(encoding="utf-8")

# Où ouvrir pour corriger un sujet de verifier() ; à défaut, c'est un modèle.
# Chemins depuis la racine de l'INSTANCE, prefixe compris : presque tout vit
# sous config/, mais pas le modele generique de fiche salarie, verse avec le
# code. Mettre « config/ » dans le format d'affichage le lui collait aussi.
FICHIERS = {"secret": "config/instance.json", "compte RH": "config/instance.json",
            "comptes": "config/instance.json", "mails": "config/instance.json",
            "config_version": "config/instance.json", "derives →": "config/instance.json",
            "templates →": "config/instance.json",
            "poste «": "config/instance.json", "url": "config/instance.json",
            "stockage": "config/instance.json", "conservation": "config/instance.json",
            "établissements": "config/societes.json",
            "roles": "config/formulaire.json", "grille": "config/grille.json",
            "fiche salarié": "modeles/fiche_salarie.docx"}
REPARABLES = ("secret", "stockage", "conservation", "url", "mails", "comptes",
              "compte RH", "établissements")


def fichier(sujet):
    """Chemin à ouvrir pour corriger ce sujet, depuis la racine de l'instance.
    Sujet inconnu = un modèle que le client doit déposer."""
    return next((f for p, f in FICHIERS.items() if sujet.startswith(p)),
                f"config/contrats/{sujet}")


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
        print(f"! {sujet}  [{fichier(sujet)}]")
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
    for a in avertissements():
        print(f"⚠ {a}")
    lignes = doctor.examiner() + [doctor.fiche()]
    print(doctor.rapport(lignes))
    code = doctor.verdict(lignes)
    print("\nOK" if code == 0 else "\nKO — un cas ne produit pas son contrat (voir doctor).")
    return code


def avertissements():
    """Ce que verifier() accepte parce que c'est légitime en démo ou en test,
    mais faux sur une instance client en production. Mesuré le 2026-09-14 :
    bilan OK sur chacun. Jamais bloquant -- le code de sortie ne change pas."""
    import mails
    m = config.instance().get("mails") or {}
    out = []
    if mails.mode() == "console":
        out.append("mails.mode = « console » : aucun mail ne part. En production : "
                   "« smtp », après un essai avec `configurer.py mail <adresse>`  "
                   "[config/instance.json]")
    sans_manager = [f"{s['nom']} / {e['nom']}" for s in config.societes()
                    for e in s["etablissements"] if not e.get("manager_email")]
    if sans_manager:
        out.append(f"manager_email absent ({', '.join(sans_manager)}) : le lien de "
                   "correction d'un rejet part à l'adresse tapée dans le formulaire "
                   "public  [config/societes.json]")
    if not m.get("comptable_defaut"):
        if sans_cabinet := [s["nom"] for s in config.societes() if not s.get("comptable_email")]:
            out.append(f"aucun cabinet comptable pour {', '.join(sans_cabinet)} : le mail "
                       "hebdomadaire (recap.py) échouera  [config/societes.json]")
    url = config.url_publique()
    if url and not url.startswith("https://"):
        out.append(f"url « {url} » n'est pas en https : les liens du mail hebdomadaire "
                   "partiraient en clair  [config/instance.json]")
    if not config.conservation():
        out.append("aucun bloc « conservation » : les pièces ne sont jamais purgées "
                   "(RGPD)  [config/instance.json]")
    return out


def mail(adresse):
    """Envoi de test en SMTP quel que soit mails.mode : c'est l'essai à faire
    AVANT de passer l'instance en smtp. Gabarit nouvelle_soumission, présent
    dans toute instance."""
    import mails
    os.environ["MAILS_MODE"] = "smtp"           # mails.envoyer relit l'env à chaque envoi
    m = config.instance().get("mails") or {}
    relais = f"{m.get('hote')}:{m.get('port')}"
    if not m.get("hote"):
        print("ÉCHEC — mails.hote vide : renseignez le relais SMTP (hote, port, "
              "utilisateur, mot_de_passe)  [config/instance.json]")
        return 1
    ok, raison = mails.envoyer("nouvelle_soumission", adresse, nom="Test d'envoi Contrat_Gen",
                               id="test", lien=config.url_publique() or "-")
    if ok:
        print(f"Envoyé à {adresse} via {relais} (gabarit nouvelle_soumission). "
              "Vérifiez la réception, spams compris.")
        return 0
    print(f"ÉCHEC via {relais} — {raison}")
    if str(m.get("port")) == "465":
        print("  Le port 465 (SSL implicite) n'est pas pris en charge : utilisez 587.")
    return 1


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


def _reparer_stockage(_raisons):
    rep = _demander("Où vivent les données ? [1] dans l'instance  "
                    "[2] dossier synchronisé (OneDrive, SharePoint, Google Drive)")
    if rep == "1":
        _ecrire("instance.json", lambda d: {**d, "stockage": {"mode": "local"}})
    elif rep == "2" and (chemin := _demander(
            "Chemin du dossier répliqué par le client de synchronisation")):
        _ecrire("instance.json",
                lambda d: {**d, "stockage": {"mode": "dossier", "chemin": chemin}})


def _reparer_conservation(_raisons):
    rep = _demander("Effacer les pièces des dossiers terminés au bout de combien "
                    "de jours ? (ex. 1095 = 3 ans ; vide = ne pas configurer)")
    if not rep:
        return
    try:
        jours = int(rep)
        cand = int(_demander("Et les candidatures jamais validées ? (ex. 730)") or rep)
    except ValueError:
        print("  Nombre invalide — rien n'est écrit.")
        return
    _ecrire("instance.json", lambda d: {**d, "conservation": {
        "jours": jours, "jours_candidature": cand,
        "apres": ["RemisComptable", "Rejetee", "Abandonnee"]}})
    print("  conservation écrite (états : RemisComptable, Rejetee, Abandonnee).")


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


REPARATIONS = {"secret": _reparer_secret, "stockage": _reparer_stockage,
               "conservation": _reparer_conservation,
               "url": _reparer_url, "mails": _reparer_mails,
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
    if len(args) == 2 and args[0] == "mail":
        sys.exit(mail(args[1]))
    sys.exit(__doc__)
