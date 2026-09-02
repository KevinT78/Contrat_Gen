"""Moteur de contrats : remplit les {{Placeholder}} d'un template .docx.

`paragraphes` et `remplacer` sont repris de wingstop_/contrat.py (le seul bloc
reellement reemployable, zero reseau) ; les regles de balisage calibrees sur la
prose Wingstop ne sont PAS reprises -- ici les templates arrivent deja balises.
"""
import re
from datetime import date
from pathlib import Path

from docx import Document

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]

# Placeholders que l'app calcule elle-meme (jamais saisis au formulaire).
CALCULES = ("Aujourdhui", "FaitLe", "NomPrenom", "NomNaissanceUsage")


def paragraphes(doc):
    """Corps, tableaux, en-tetes et pieds de page -- chaque paragraphe une fois."""
    # dict et pas set d'id() : garder une reference vivante sur chaque paragraphe,
    # sinon lxml libere le proxy et reattribue son id() a un autre element, qui
    # est alors saute en silence.
    vus = {}

    def des(conteneur):
        for p in conteneur.paragraphs:
            if id(p._p) not in vus:
                vus[id(p._p)] = p
                yield p
        for t in conteneur.tables:
            for row in t.rows:
                for cell in row.cells:
                    yield from des(cell)

    yield from des(doc)
    for s in doc.sections:
        for partie in (s.header, s.footer, s.first_page_header, s.first_page_footer):
            yield from des(partie)


def remplacer(p, motif, repl, limite=10):
    """Remplace dans un paragraphe en ne touchant que les runs couverts par le
    match : sinon Word perd le gras, l'italique et les sauts de ligne du reste."""
    n, depuis = 0, 0
    while n < limite:
        runs = p.runs
        texte = "".join(r.text for r in runs)
        m = motif.search(texte, depuis)
        if not m:
            return n
        nouveau, debut, fin = m.expand(repl), m.start(), m.end()
        if texte[debut:fin] == nouveau:
            return n                        # rien a changer, et surtout pas de boucle
        depuis = debut + len(nouveau)
        pos = 0
        for r in runs:
            d, f = pos, pos + len(r.text)
            pos = f
            if f <= debut or d >= fin:      # run entierement hors du match
                continue
            r.text = r.text[:max(0, debut - d)] + nouveau + (r.text[fin - d:] if f > fin else "")
            nouveau = ""                    # le reste du match est efface
        n += 1
    return n


def date_fr(iso):
    """2026-09-02 -> 2 septembre 2026. Rend la chaine telle quelle si non ISO."""
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or ""
    return f"{'1er' if d.day == 1 else d.day} {MOIS_FR[d.month - 1]} {d.year}"


def calculees(champs):
    """Le jeu fixe de valeurs derivees, en code produit (per ticket 05)."""
    nom = (champs.get("nom_usage") or champs.get("nom_naissance") or "").upper()
    prenom = champs.get("prenom", "")
    return {
        "Aujourdhui": date_fr(date.today().isoformat()),
        "FaitLe": date_fr(date.today().isoformat()),
        "NomPrenom": f"{prenom} {nom}".strip(),
        "NomNaissanceUsage": (champs.get("nom_naissance") or "").upper(),
    }


def valeurs(champs, mentions, extra=None):
    """champs (ids du schema) + mentions societe/etablissement -> placeholders.

    `extra` : placeholders calcules par l'appelant qui ne sont ni des champs ni
    des mentions (ex. {{PiecesFournies}} / {{PiecesManquantes}} de la fiche
    salarie, qui dependent de l'etat du disque)."""
    import config

    vals = dict(mentions)
    for c in config.champs():
        ph = c.get("placeholder")
        if not ph:
            continue
        v = champs.get(c["id"], "")
        vals[ph] = date_fr(v) if c["type"] == "date" else str(v)
    vals.update(calculees(champs))
    vals.update(extra or {})
    return vals


def generer(template, vals, dest):
    """Remplit les {{Placeholder}}. Refuse d'ecrire s'il en reste un : un contrat
    troue -- ou pire, portant le nom de l'ancien salarie -- ne doit pas sortir."""
    doc = Document(str(template))
    for p in paragraphes(doc):
        for cle, val in vals.items():
            remplacer(p, re.compile(r"\{\{" + re.escape(cle) + r"\}\}"),
                      str(val).replace("\\", r"\\"))
    restants = sorted({m for p in paragraphes(doc)
                       for m in re.findall(r"\{\{[^}\n]{0,40}\}\}", p.text)})
    if restants:
        raise ValueError(f"placeholders non remplis dans {Path(template).name} : "
                         + ", ".join(restants))
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest
