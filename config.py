"""Configuration du client : tout ce qui varie d'un client à l'autre vit en
fichiers sous config/, jamais en code.

Un client = une copie de ce dépôt. `python installer.py "<Client>"` remet
config/ à blanc et génère les secrets ; on remplit ensuite societes.json,
instance.json et on dépose les .docx.
"""
import json
import os
import re
from pathlib import Path

RACINE = Path(__file__).parent
CLIENT = Path(os.environ.get("CONFIG_DIR", RACINE / "config"))
DONNEES = Path(os.environ.get("DONNEES", RACINE / "data"))

SECRET_A_INSTALLER = "REMPLACER-A-L-INSTALLATION"
MDP_PAR_DEFAUT = "demo"          # l'ancien mot de passe de démo — refusé au démarrage

# Version du FORMAT de config/, pas du produit. Le code est un paquet versionne,
# config/ vit chez le client : une config ecrite pour un format qu'on ne
# comprend plus doit refuser de demarrer en disant quoi faire, pas se faire
# reecrire dans le dos (l'app n'ecrit jamais sa config).
CONFIG_VERSION = 1

# Cache simple plutot que lru_cache : `recharger()` a besoin de reposer
# l'ancienne config si la nouvelle est invalide, ce qu'un cache_clear() ne
# permet pas.
_CACHE = {}


def _lire(nom):
    if nom not in _CACHE:
        _CACHE[nom] = json.loads((CLIENT / nom).read_text(encoding="utf-8"))
    return _CACHE[nom]


def instance():
    return _lire("instance.json")


def formulaire():
    return _lire("formulaire.json")


def societes():
    return _lire("societes.json")


def recharger():
    """Relit config/ a chaud, sans redemarrer. Renvoie {} si la nouvelle config
    est valide (elle est alors active), sinon les manques -- et l'ancienne
    config reste en place : une instance qui servait continue de servir."""
    ancien = dict(_CACHE)
    _CACHE.clear()
    if manques := verifier():
        _CACHE.clear()
        _CACHE.update(ancien)
        return manques
    return {}


def etablissements():
    """[(cle, libelle)] pour le select du formulaire. Cle = 'Societe / Etab'."""
    return [(f"{s['nom']} / {e['nom']}", f"{s['nom']} — {e['nom']}")
            for s in societes() for e in s["etablissements"]]


def etablissement(cle):
    """Retrouve (societe, etablissement) depuis la cle du formulaire."""
    for s in societes():
        for e in s["etablissements"]:
            if f"{s['nom']} / {e['nom']}" == cle:
                return s, e
    raise KeyError(f"etablissement inconnu : {cle}")


def mentions(cle):
    """Placeholders legaux de la societe + de l'etablissement, pour le contrat."""
    s, e = etablissement(cle)
    return {**s.get("mentions", {}), **e.get("mentions", {}),
            "Siret": e.get("siret", ""), "Siren": s.get("siren", ""),
            "Etablissement": e["nom"], "Societe": s["nom"]}


def comptable(cle):
    s, _ = etablissement(cle)
    return s.get("comptable_email") or instance()["mails"]["comptable_defaut"]


def mode_contrat(cle):
    """Couloir du schema, en donnee : 'genere' (Dark Kitchen) ou 'depose'
    (Restaurant, contrat fait a la main sur myrhis). Absent = 'genere'."""
    _, e = etablissement(cle)
    return e.get("contrat", "genere")


def modele_pour(champs):
    """Poste (+ conditions eventuelles) -> nom du modele de contrat, ou None.

    Deux formes acceptees pour instance()['templates'] :
      - dict {poste: fichier}                              (client simple)
      - liste [{"quand": {champ: valeur, ...}, "modele": fichier}, ...]
        premiere regle dont TOUS les champs 'quand' collent ; une regle sans
        'quand' est un fourre-tout. Sert au branchement temps partiel, ou a un
        modele par etablissement quand les mentions sont figees dans la prose.

    Comparaison insensible a la casse et aux accents : « Equipier polyvalent »
    exporte par MS Forms matche « Equipier Polyvalent », « Oui » matche « OUI ».
    """
    from contrat import sans_accent
    def n(x):
        return sans_accent(x).casefold().strip()

    t = instance()["templates"]
    if isinstance(t, dict):
        return t.get(valeur(champs, "poste"))
    for regle in t:
        if all(n(champs.get(k, "")) == n(v)
               for k, v in regle.get("quand", {}).items()):
            return regle.get("modele")
    return None


def derives():
    """Placeholders calcules par regle en config (formateurs, blocs conditionnels)."""
    return instance().get("derives", [])


def saisie_rh():
    """Placeholders qu'aucune question du formulaire ne fournit : la RH les
    saisit a la generation du contrat."""
    return instance().get("saisie_rh", [])


def critiques():
    """Placeholders qui, vides, produisent un contrat sans objet (ligne de paie
    blanche, « demeurant au . »). Derives des champs requis + liste explicite
    instance()['critiques'] (pour ce qui ne vient pas d'un champ, ex. le salaire
    tire de la grille)."""
    ph = {c["placeholder"] for c in champs()
          if c.get("requis") and c.get("placeholder")}
    return ph | set(instance().get("critiques", []))


def grille():
    """Grille de remuneration du client (salaire par poste). {} si absente : un
    client peut faire saisir le salaire a la RH plutot que le tirer d'une grille."""
    if "grille.json" not in _CACHE:
        f = CLIENT / "grille.json"
        _CACHE["grille.json"] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _CACHE["grille.json"]


def champs():
    return formulaire()["champs"]


# Le vocabulaire ferme des roles que le MOTEUR sait lire, et l'id historique de
# chacun. Tout le reste du formulaire est du champ libre : le moteur l'ignore et
# il ne va qu'aux templates. C'est ce qui rend le produit installable chez une
# PME dont les champs s'appellent autrement -- avant, « etablissement » et
# « poste » etaient lus en dur a 13 endroits et un renommage levait un KeyError
# en pleine action RH.
ROLES = {"etablissement": "etablissement", "poste": "poste",
         "nom": "nom_naissance", "prenom": "prenom", "nom_usage": "nom_usage",
         "nom_naissance": "nom_naissance", "email": "email_demandeur",
         "date_debut": "date_debut"}

# Sans eux le parcours ne peut pas tourner : l'etablissement route les mentions
# et le comptable, le poste route le modele et la grille, le nom et l'email
# adressent les mails. Les autres sont facultatifs (un formulaire a champ unique
# « NOM Prenom » n'a ni prenom ni nom d'usage).
ROLES_REQUIS = ("etablissement", "poste", "nom", "email")


def role(nom):
    """id du champ qui joue un role attendu par le code. Declare dans
    formulaire()['roles'] ; a defaut l'id historique de ROLES -- une config sans
    'roles' garde le comportement d'avant.

    Rend TOUJOURS une chaine, meme sur une config malformee : `roles` qui n'est
    pas un objet, ou un id qui est une liste. Sinon chaque appelant explose a sa
    facon (`x not in ids` -> TypeError) -- y compris verifier(), dont c'est le
    travail de refuser cette config proprement."""
    declares = formulaire().get("roles") or {}
    declare = declares.get(nom) if isinstance(declares, dict) else None
    return declare if isinstance(declare, str) and declare else ROLES.get(nom) or nom


def valeur(champs, nom, defaut=""):
    """La valeur qu'un dossier porte pour un role. LE seul acces du moteur aux
    champs d'un salarie : aucun id de champ n'est ecrit en dur ailleurs."""
    return champs.get(role(nom), defaut)


def identite(champs):
    """(prenom, nom) d'un dossier, quel que soit le decoupage du formulaire du
    client : champs separes, ou champ unique « NOM Prenom » (-> ("", "NOM
    Prenom")). Les trois modules qui affichaient un nom le construisaient chacun
    de leur cote, et aucun ne retombait sur le role « nom » : un client sans
    champ « nom de naissance » sortait des contrats au prenom seul."""
    nom = (valeur(champs, "nom_usage") or valeur(champs, "nom_naissance")
           or valeur(champs, "nom"))
    return valeur(champs, "prenom"), nom


def champ(cid):
    for c in champs():
        if c["id"] == cid:
            return c
    # cle hors schema (ex. une saisie RH fusionnee dans champs) : libelle = la
    # cle elle-meme, plutot qu'un KeyError qui casse l'ecran dossier.
    return {"id": cid, "libelle": cid, "type": "texte"}


def pieces():
    return [c for c in champs() if c["type"] == "piece_jointe"]


def secret():
    return instance()["secret"].encode()


def _templates_actifs():
    """Les modeles dont un placeholder manquant doit bloquer le demarrage :
    les contrats (par poste ou par regle), plus la fiche salarie si declaree."""
    t = instance()["templates"]
    noms = set(t.values()) if isinstance(t, dict) \
        else {r["modele"] for r in t if r.get("modele")}
    if fiche := instance().get("fiche_salarie"):
        noms.add(fiche)
    return noms


# Volontairement laxiste : on veut VOIR « {{ Nom }} » et « {{nom}} » pour les
# refuser au demarrage. Une regex stricte (\w+) les rend invisibles au garde-fou
# et les laisse exploser a la generation du contrat d'un vrai salarie -- c'est
# le mode d'echec a eviter quand c'est le CLIENT qui balise ses .docx.
JETON = re.compile(r"\{\{([^}\n]{0,60})\}\}")


def placeholders_connus():
    """{placeholder: d'ou vient sa valeur} pour CETTE config. Deux usages :
    le garde-fou de demarrage, et la fiche remise au client qui balise ses
    propres .docx (`python placeholders.py`)."""
    import contrat

    src = {}
    for c in champs():
        if c.get("placeholder"):
            src[c["placeholder"]] = f"Question « {c['libelle']} » du formulaire"
    for p in contrat.CALCULES:
        src[p] = "Calculé automatiquement"
    src["PiecesFournies"] = "Liste des pièces jointes fournies"
    src["PiecesManquantes"] = "Liste des pièces jointes manquantes"
    for d in derives():
        # .get : une regle sans placeholder est une config invalide, que
        # verifier() rapporte -- elle ne doit pas faire planter le rapport.
        if ph := d.get("placeholder"):
            src[ph] = "Règle dérivée (instance.json → derives)"
    for p in saisie_rh():
        src[p] = "Saisi par la RH au moment de générer le contrat"
    if grille():
        src["SalaireChiffres"] = "Grille de salaires (config/grille.json)"
        src["SalaireLettres"] = "Grille de salaires, en toutes lettres"
    for s in societes():
        for p in s.get("mentions", {}):
            src[p] = "Mention légale de la société (societes.json)"
        for e in s["etablissements"]:
            for p in e.get("mentions", {}):
                src[p] = "Mention légale de l'établissement (societes.json)"
    for p in ("Societe", "Siren", "Etablissement", "Siret"):
        src[p] = "Identité de la société / de l'établissement choisi"
    return src


def _jetons(chemin):
    """Les {{jetons}} bruts d'un template, tels qu'ecrits -- espaces compris."""
    if chemin.suffix.lower() == ".docx":
        from docx import Document
        import contrat
        texte = "\n".join(p.text for p in contrat.paragraphes(Document(chemin)))
    else:
        texte = chemin.read_text(encoding="utf-8")
    return set(JETON.findall(texte))


def _verifier_version():
    v = instance().get("config_version", CONFIG_VERSION)
    if v != CONFIG_VERSION:
        return {"config_version": [
            f"config/ est au format {v}, ce code attend le format "
            f"{CONFIG_VERSION} — mettez à jour config/instance.json "
            f"(voir CHANGELOG) ou réinstallez la version correspondante"]}
    return {}


def _verifier_installation():
    from werkzeug.security import check_password_hash
    manques = {}
    if SECRET_A_INSTALLER in instance().get("secret", ""):
        manques["secret"] = [f"encore « {SECRET_A_INSTALLER} » — lancez : "
                             f"python installer.py \"<Client>\""]
    if any(check_password_hash(u["mdp_hash"], MDP_PAR_DEFAUT)
           for u in instance().get("utilisateurs", {}).values()):
        manques["compte RH"] = ["mot de passe par défaut — changez mdp_hash "
                                "dans config/instance.json"]
    if not etablissements():
        manques["établissements"] = ["aucun établissement dans config/societes.json"]
    return manques


def _verifier_derives():
    """Verifie la FORME des regles derives avant leurs valeurs -- sinon c'est le
    garde-fou lui-meme qui plante sur `si: ["nom"]`."""
    import contrat
    manques = {}
    for d in derives():
        ph = d.get("placeholder")
        raisons = [] if ph else ["règle sans « placeholder » : rien à alimenter"]
        if "format" in d:
            if d["format"] not in contrat.FORMATEURS:
                raisons.append(f"formateur « {d['format']} » inconnu — au choix : "
                               + ", ".join(sorted(contrat.FORMATEURS)))
            if not d.get("de"):
                raisons.append("un « format » exige « de » : le champ à formater")
        else:
            if "alors" not in d:
                raisons.append("ni « format » ni « alors » : la règle ne produit rien")
            si = d.get("si")
            if si is None:
                pass
            elif not isinstance(si, (list, tuple)) or len(si) != 3:
                raisons.append("« si » attend [champ, opérateur, valeur]")
            elif si[1] not in contrat.OPERATEURS:
                raisons.append(f"opérateur « {si[1]} » inconnu — au choix : "
                               + ", ".join(contrat.OPERATEURS))
        if raisons:
            manques[f"derives → {ph or '?'}"] = raisons
    return manques


def _verifier_roles():
    """Les roles sont la promesse « n'importe quelle PME » : le moteur ne lit que
    des roles, jamais un id de champ. La FORME avant les valeurs."""
    manques = {}
    declares = formulaire().get("roles", {})
    if not isinstance(declares, dict):
        manques["roles"] = ["formulaire.json → « roles » attend un objet "
                            "{rôle: id de champ}"]
        return manques
    ids = {c["id"] for c in champs()}
    raisons = []
    for nom, cid in declares.items():
        if nom not in ROLES:
            raisons.append(f"rôle « {nom} » inconnu — au choix : "
                           + ", ".join(sorted(ROLES)))
        elif not isinstance(cid, str) or not cid:
            raisons.append(f"rôle « {nom} » : « {cid} » n'est pas un id de champ")
        elif cid not in ids:
            raisons.append(f"rôle « {nom} » → « {cid} », qui n'est pas un "
                           "champ du formulaire")
    for nom in ROLES_REQUIS:
        if role(nom) not in ids:
            raisons.append(f"rôle « {nom} » : aucun champ « {role(nom)} » — "
                           f"déclarez-le dans formulaire.json → roles")
    if raisons:
        manques["roles"] = raisons
    return manques


def _verifier_placeholders():
    """Un placeholder de .docx actif qu'aucune source n'alimente : la promesse
    d'adaptabilite du produit, verifiee au demarrage."""
    manques = {}
    sources = placeholders_connus()
    for nom in _templates_actifs():
        chemin = CLIENT / "contrats" / nom
        if not chemin.exists():
            manques[nom] = ["fichier absent de config/contrats/"]
            continue
        raisons = []
        for j in sorted(_jetons(chemin)):
            if j in sources:
                continue
            proche = next((s for s in sources if s.casefold() == j.strip().casefold()),
                          None)
            raisons.append(f"{{{{{j}}}}} → écrire exactement {{{{{proche}}}}}"
                           if proche else f"{{{{{j}}}}} — aucune source ne l'alimente")
        if raisons:
            manques[nom] = raisons
    return manques


def _verifier_postes():
    """Chaque poste du formulaire doit atteindre un modele."""
    manques = {}
    t = instance()["templates"]
    if isinstance(t, list):
        vises = set()
        fourre_tout = any(not r.get("quand") for r in t)
        for r in t:
            vises |= {str(v) for v in r.get("quand", {}).values()}
        for c in champs():
            if c["id"] != role("poste"):
                continue
            for opt in c.get("options", []):
                if not fourre_tout and opt not in vises:
                    manques[f"poste « {opt} »"] = ["aucune règle de template ne le vise"]
    return manques


def verifier():
    """Garde-fou de demarrage. Renvoie {} si tout va bien, sinon un dict
    {sujet: [raisons]} et l'instance ne sert pas."""
    manques = {}
    for check in (_verifier_version, _verifier_installation, _verifier_derives,
                  _verifier_roles, _verifier_placeholders, _verifier_postes):
        manques.update(check())
    return manques
