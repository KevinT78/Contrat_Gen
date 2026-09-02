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
CLIENT = RACINE / "config"
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


def champs():
    return formulaire()["champs"]


def champ(cid):
    for c in champs():
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def pieces():
    return [c for c in champs() if c["type"] == "piece_jointe"]


def secret():
    return instance()["secret"].encode()


def _templates_actifs():
    """Les .docx dont un placeholder manquant doit bloquer le demarrage :
    les contrats par poste, plus la fiche salarie si elle est declaree."""
    noms = set(instance()["templates"].values())
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
    for s in societes():
        sources |= set(s.get("mentions", {}))
        for e in s["etablissements"]:
            sources |= set(e.get("mentions", {}))
    sources |= {"Siret", "Siren", "Etablissement", "Societe"}

    for nom in _templates_actifs():
        if not (CLIENT / "contrats" / nom).exists():
            manques[nom] = ["fichier absent de config/contrats/"]
            continue
        doc = Document(CLIENT / "contrats" / nom)
        vus = {m for p in contrat.paragraphes(doc)
               for m in re.findall(r"\{\{(\w+)\}\}", p.text)}
        if vus - sources:
            manques[nom] = sorted(vus - sources)
    return manques
