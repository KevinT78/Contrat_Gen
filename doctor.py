"""Vérifie la couverture d'une config en PRODUISANT les contrats qu'elle donne.

    python doctor.py

Un salarié fictif est promené sur chaque croisement que la config sait
distinguer (poste × temps partiel × … × établissement) et le contrat est
réellement généré dans un dossier temporaire. Trois issues par ligne :

    Manager  / NON / ACME — Siège    ✓ → …/doctor/01-Manager.txt
    Stagiaire / NON / ACME — Siège   ✗ aucun modèle ne vise ce cas
    Equipier / OUI / ACME — Siège    ⚠ valeurs vides dans … : SalaireChiffres

C'est le livrable de la relecture juridique : le client relit les contrats que
sa config produit vraiment, pas un modèle abstrait. À rejouer après chaque
modification de config/. Lecture seule côté données — rien n'est écrit hors du
dossier temporaire, la commande est sûre sur une instance en production.

Simplification volontaire : les axes sont les seuls champs que les règles de
template savent lire ; les autres prennent une valeur d'exemple fixe. Faire
varier tout le formulaire donnerait un produit cartésien illisible pour un
signal identique — à revoir si un client fait dépendre sa prose d'un champ
qui ne décide pas du modèle.
"""
import shutil
import sys
import tempfile
from datetime import date
from itertools import product
from pathlib import Path

import config
import contrat
import store

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DOSSIER = Path(tempfile.gettempdir()) / "contrat-gen-doctor"

# Valeur d'exemple par type de champ : plausible, visiblement fictive.
EXEMPLES = {"texte": "Exemple", "texte_long": "Exemple", "email": "exemple@example.test",
            "date": date.today().isoformat(), "nombre": "35", "montant": "2000",
            "choix": ""}

MARQUES = {"ok": "✓", "sans_modele": "✗", "refuse": "⚠", "ignore": "–"}


def _id_etab():
    return config.role("etablissement")


def _regles():
    """Les règles `templates` sous forme de liste, la forme dict normalisée."""
    t = config.instance()["templates"]
    return ([{"quand": {config.role("poste"): p}, "modele": m} for p, m in t.items()]
            if isinstance(t, dict) else t)


def axes():
    """Les champs qui peuvent changer le modèle choisi, donc les colonnes du
    tableau. Dérivés des règles du client : les clés de `quand`. L'établissement
    en est EXCLU — c'est déjà l'axe extérieur, et le remettre en colonne
    écrasait la vraie clé par une valeur d'exemple, si bien qu'une règle visant
    un établissement ne matchait plus jamais."""
    exclus = {_id_etab()}
    noms = [k for r in _regles() for k in r.get("quand", {}) if k not in exclus]
    return list(dict.fromkeys(noms)) or [config.role("poste")]


def _exemple(cid):
    c = config.champ(cid)
    return (c.get("options") or [EXEMPLES.get(c["type"], "Exemple")])[0]


def valeurs_axe(cid):
    """Les valeurs à essayer sur un axe : les options du champ ET celles que les
    règles nomment. Les seules options ne suffisent pas — un client dont le
    poste est du texte libre n'en a aucune, et doctor testerait alors un poste
    inventé avant d'annoncer « tout est couvert ». L'union montre les deux
    trous : une option qu'aucune règle ne vise, une règle que le formulaire ne
    permet plus d'atteindre."""
    citees = [str(r["quand"][cid]) for r in _regles() if cid in r.get("quand", {})]
    vues = list(config.champ(cid).get("options") or []) + citees
    return list(dict.fromkeys(vues)) or [_exemple(cid)]


def salarie_fictif():
    """Un jeu de champs complet, hors pièces jointes (le contrat n'en lit pas)."""
    return {c["id"]: _exemple(c["id"]) for c in config.champs()
            if c["type"] != "piece_jointe"}


def cas():
    """Le produit cartésien établissements × valeurs de chaque axe."""
    colonnes = axes()
    valeurs = [valeurs_axe(cid) for cid in colonnes]
    base = salarie_fictif()
    id_etab = _id_etab()
    for cle, _ in config.etablissements():
        for combinaison in product(*valeurs):
            champs = {**base, **dict(zip(colonnes, combinaison)), id_etab: cle}
            yield cle, colonnes, champs


def examiner():
    """[{statut, libelle, detail, fichier, champs}] — une ligne par croisement.
    Génère pour de vrai : c'est le seul moyen de voir un contrat troué."""
    shutil.rmtree(DOSSIER, ignore_errors=True)
    lignes = []
    for n, (cle, colonnes, champs) in enumerate(cas(), 1):
        libelle = " / ".join([*(champs.get(c) or "—" for c in colonnes), cle])
        ligne = {"libelle": libelle, "champs": champs, "detail": "", "fichier": None}
        lignes.append(ligne)

        if config.mode_contrat(cle) != "genere":
            ligne.update(statut="ignore", detail="contrat déposé (fait hors de l'app)")
            continue
        modele = config.modele_pour(champs)
        if not modele:
            ligne.update(statut="sans_modele", detail="aucun modèle ne vise ce cas "
                                                         "(instance.json → templates)")
            continue

        # Ce que la RH tape au moment de générer : marqué comme tel, pour que le
        # relecteur voie où sa saisie atterrit dans le contrat.
        extra = {p: f"«{p}»" for p in config.saisie_rh()}
        extra.update(PiecesFournies="(pièces fournies)", PiecesManquantes="aucune")
        # Nom de fichier lisible ET sain : le libellé contient des « / » qui
        # feraient des dossiers, et des accents que Word n'aime pas partout.
        court = store.SAIN.sub("-", contrat.sans_accent(libelle))[:40].strip("-")
        dest = DOSSIER / f"{n:02d}-{court}.docx"          # generer sort toujours du .docx
        try:
            vals = contrat.valeurs(champs, config.mentions(cle), extra=extra)
            contrat.generer(config.CLIENT / "contrats" / modele, vals, dest)
            ligne.update(statut="ok", fichier=str(dest), detail=modele)
        except (ValueError, OSError, KeyError) as e:
            # Le refus de contrat.generer nomme deja le modele : ne pas le repeter.
            ligne.update(statut="refuse",
                         detail=str(e) if modele in str(e) else f"{modele} : {e}")
    return lignes


def verdict(lignes):
    """Code de sortie : non nul dès qu'un cas ne produit pas son contrat."""
    return 1 if any(l["statut"] in ("sans_modele", "refuse") for l in lignes) else 0


def rapport(lignes):
    large = max((len(l["libelle"]) for l in lignes), default=0)
    out = [f"Couverture de « {config.instance()['client']} » — "
           f"{len(lignes)} cas, contrats dans {DOSSIER}"]
    # La duree hebdo decide la ligne de bareme : un « salaire vide » se lit mal
    # sans savoir laquelle a ete essayee.
    if ch := config.grille().get("champ_heures"):
        out.append(f"Salarié fictif : {config.champ(ch)['libelle']} = "
                   f"{_exemple(ch)}, autres champs « Exemple ».")
    out.append("")
    for l in lignes:
        droite = Path(l["fichier"]).name if l["fichier"] else l["detail"]
        out.append(f"  {l['libelle']:<{large}}  {MARQUES[l['statut']]} {droite}")
    # Les cas « contrat déposé » sont hors compte : ils ne produisent aucun
    # contrat et n'ont pas à en produire. Les inclure dans le total donnait un
    # « 3 cas sur 9 produisent un contrat » ou les 3 etaient justement ceux qui
    # n'en produisent pas.
    ok = sum(1 for l in lignes if l["statut"] == "ok")
    hors = sum(1 for l in lignes if l["statut"] == "ignore")
    attendus = len(lignes) - hors
    reste = f" ({hors} en contrat déposé, hors compte)" if hors else ""
    if not attendus:            # tous les établissements en contrat déposé
        out.append(f"\nAucun contrat à produire{reste}.")
    elif ok < attendus:
        out.append(f"\n{ok} cas sur {attendus} produisent un contrat{reste}.")
    else:
        out.append(f"\nLes {attendus} cas produisent un contrat{reste}.")
    return "\n".join(out)


if __name__ == "__main__":
    if manques := config.verifier():
        print("Config refusée au démarrage — doctor tourne quand même :", file=sys.stderr)
        for sujet, raisons in manques.items():
            print(f"  ! {sujet} : {'; '.join(raisons)}", file=sys.stderr)
        print(file=sys.stderr)
    lignes = examiner()
    print(rapport(lignes))
    sys.exit(verdict(lignes))
