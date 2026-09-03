"""Configuration du client : tout ce qui varie d'un client à l'autre vit en
fichiers sous config/, jamais en code.

Un client = une copie de ce dépôt. `python installer.py "<Client>"` remet
config/ à blanc et génère les secrets ; on remplit ensuite societes.json,
instance.json et on dépose les .docx.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

RACINE = Path(__file__).parent
CLIENT = Path(os.environ.get("CONFIG_DIR", RACINE / "config"))
DONNEES = Path(os.environ.get("DONNEES", RACINE / "data"))

SECRET_A_INSTALLER = "REMPLACER-A-L-INSTALLATION"
MDP_PAR_DEFAUT = "demo"          # l'ancien mot de passe de démo — refusé au démarrage


# ponytail: config lue une fois au demarrage (lru_cache) -- editer un .json
# demande un restart. Un rechargement a chaud quand un ecran de service
# existera (ticket 11).
def _lire(nom):
    return json.loads((CLIENT / nom).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def instance():
    return _lire("instance.json")


@lru_cache(maxsize=None)
def formulaire():
    return _lire("formulaire.json")


@lru_cache(maxsize=None)
def societes():
    return _lire("societes.json")


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
        return t.get(champs.get("poste"))
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


@lru_cache(maxsize=None)
def grille():
    """Grille de remuneration du client (salaire par poste). {} si absente : un
    client peut faire saisir le salaire a la RH plutot que le tirer d'une grille."""
    f = CLIENT / "grille.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def champs():
    return formulaire()["champs"]


def role(nom, defaut=None):
    """id du champ qui joue un role attendu par le code (nom affiche, email de
    reponse...). Declare dans formulaire()['roles'] ; a defaut, l'id historique
    passe en `defaut` -- une config sans 'roles' garde le comportement d'avant."""
    return formulaire().get("roles", {}).get(nom) or defaut or nom


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


def verifier():
    """Garde-fou de demarrage. Renvoie {} si tout va bien, sinon un dict
    {sujet: [raisons]} et l'instance ne sert pas.

    Deux familles de refus :
      - installation incomplete (secret/mdp par defaut, aucun etablissement) :
        un secret par defaut rend les liens du lot comptable forgeables, un mdp
        par defaut ouvre des pieces d'identite -- frontiere de confiance.
      - un placeholder de .docx actif qu'aucune source n'alimente : la promesse
        d'adaptabilite du produit, verifiee au demarrage et pas a la generation
        du contrat d'un vrai salarie.
    """
    import re
    from docx import Document
    from werkzeug.security import check_password_hash
    import contrat

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

    sources = {c["placeholder"] for c in champs() if c.get("placeholder")}
    sources |= set(contrat.CALCULES)
    sources |= {"PiecesFournies", "PiecesManquantes"}
    sources |= {d["placeholder"] for d in derives()}
    sources |= set(saisie_rh())
    if grille():
        sources |= {"SalaireChiffres", "SalaireLettres"}
    for s in societes():
        sources |= set(s.get("mentions", {}))
        for e in s["etablissements"]:
            sources |= set(e.get("mentions", {}))
    sources |= {"Siret", "Siren", "Etablissement", "Societe"}

    for nom in _templates_actifs():
        chemin = CLIENT / "contrats" / nom
        if not chemin.exists():
            manques[nom] = ["fichier absent de config/contrats/"]
            continue
        if chemin.suffix.lower() == ".docx":
            doc = Document(chemin)
            vus = {m for p in contrat.paragraphes(doc)
                   for m in re.findall(r"\{\{(\w+)\}\}", p.text)}
        else:
            vus = set(re.findall(r"\{\{(\w+)\}\}",
                                 chemin.read_text(encoding="utf-8")))
        if vus - sources:
            manques[nom] = sorted(vus - sources)

    # Chaque poste du formulaire doit atteindre un modele : « Leavers » sans
    # regle bloque le demarrage, jamais la generation d'un vrai contrat.
    t = instance()["templates"]
    if isinstance(t, list):
        vises = set()
        fourre_tout = any(not r.get("quand") for r in t)
        for r in t:
            vises |= {str(v) for v in r.get("quand", {}).values()}
        for c in champs():
            if c["id"] != "poste":
                continue
            for opt in c.get("options", []):
                if not fourre_tout and opt not in vises:
                    manques[f"poste « {opt} »"] = ["aucune règle de template ne le vise"]
    return manques
