"""Bouton « Compléter les dossiers » : lit les exports Excel de la RH (logiciel RH,
planning) pour remplir les champs des salariés importés depuis le SharePoint.

Stdlib seule : un .xlsx est un zip de XML. Seule la 1re feuille est lue. Chaque
ligne qui contient « Nom » et « Prénom » est un en-tête, valable jusqu'au
suivant (les sections d'une synthèse de planning). Les colonnes reconnues sont
converties vers les champs du formulaire du client ; une valeur non reconnue
part en « rejet » : affichée à la RH, jamais écrite.
"""
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

import config
import sharepoint

TAILLE_MAX = 20 * 1024 * 1024    # par membre du zip : une bombe zip s'arrête là
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _cle(entete):
    return re.sub("[^a-z0-9]", "", sharepoint._norm(entete))


# --- lecture ------------------------------------------------------------------

def _xml(z, nom):
    if z.getinfo(nom).file_size > TAILLE_MAX:
        raise ValueError(f"{nom} : trop volumineux")
    return ET.fromstring(z.read(nom))


def _texte(el):
    """Texte d'une chaîne partagée ou inline : texte enrichi = tous ses <t>."""
    return "".join(t.text or "" for t in el.iterfind(".//{*}t"))


def _colonne(ref):
    n = 0
    for c in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(c) - 64
    return n - 1


def _premiere_feuille(z):
    """Chemin de la 1re feuille du classeur : son nom de fichier ne dit rien
    (un logiciel de planning range « sheet1 » en dernier dans le zip)."""
    rid = _xml(z, "xl/workbook.xml").find("{*}sheets/{*}sheet").get(REL)
    for r in _xml(z, "xl/_rels/workbook.xml.rels"):
        if r.get("Id") == rid:
            cible = r.get("Target")
            return cible.lstrip("/") if cible.startswith("/") else "xl/" + cible
    raise KeyError(rid)


def _cellules(row, partagees):
    """{n° de colonne: texte} d'une ligne. Une formule garde sa dernière valeur
    calculée ; une erreur (#N/A) reste du texte, que la conversion rejette."""
    out = {}
    for c in row.iterfind("{*}c"):
        t, v = c.get("t"), c.find("{*}v")
        if t == "inlineStr":
            val = _texte(c)
        elif v is None:
            continue
        else:
            val = partagees[int(v.text)] if t == "s" else (v.text or "")
        out[_colonne(c.get("r"))] = val.strip()
    return out


def lignes(octets):
    """[{clé d'en-tête: texte}] des lignes de salariés de la 1re feuille.
    ValueError si le fichier n'est pas un classeur lisible."""
    # simplification volontaire : ni date1904 ni cellule sans attribut r, absents
    # des 13 exports réels ; un tel fichier est refusé comme illisible
    try:
        with zipfile.ZipFile(io.BytesIO(octets)) as z:
            partagees = ([_texte(si) for si in _xml(z, "xl/sharedStrings.xml")]
                         if "xl/sharedStrings.xml" in z.namelist() else [])
            feuille = _xml(z, _premiere_feuille(z))
        rangees = [_cellules(row, partagees) for row in feuille.iterfind(".//{*}row")]
    except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError, TypeError,
            AttributeError, ValueError):
        raise ValueError("classeur .xlsx illisible") from None
    out, entete = [], None
    for cellules in rangees:
        cles = {_cle(v) for v in cellules.values()}
        if {"nom", "prenom"} <= cles:
            entete = {}
            for i, v in cellules.items():
                entete.setdefault(_cle(v), i)       # Lundi…Dimanche en double : 1re
            continue
        if entete is None:
            continue
        ligne = {k: cellules.get(i, "") for k, i in entete.items()}
        if ligne["nom"] and ligne["prenom"]:          # titres de section, groupes
            out.append(ligne)
    return out


# --- conversion vers les champs du formulaire ----------------------------------

def _premier(ligne, *cles):
    return next((ligne[k] for k in cles if ligne.get(k) and ligne[k].upper() != "N/A"), "")


def _date(v):
    """N° de série Excel, jj/mm/aa, jj/mm/aaaa ou AAAA-MM-JJ -> AAAA-MM-JJ, sinon None."""
    if re.fullmatch(r"\d+(\.\d+)?", v):
        jours = int(float(v))
        # 1900-01-01 .. 2199 : hors de là, un nombre dans une colonne de date
        return (date(1899, 12, 30) + timedelta(days=jours)).isoformat() if 2 <= jours < 109574 else None
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _mots(s):
    return re.findall(r"[a-z0-9]+", sharepoint._norm(s))


def _choix(v, options):
    """L'option qui correspond à `v`, ou None. Égalité des caractères
    alphanumériques, puis option dont les mots commencent ceux de `v` (la plus
    longue), puis l'unique option qui partage le 1er mot. Jamais d'inclusion
    lâche : « Second Manager » ne devient pas « Manager »."""
    mots = _mots(v)
    if not mots:
        return None
    for o in options:
        if _mots(o) and "".join(_mots(o)) == "".join(mots):
            return o
    prefixes = [o for o in options if _mots(o) and mots[:len(_mots(o))] == _mots(o)]
    if prefixes:
        return max(prefixes, key=lambda o: len(_mots(o)))
    memes = [o for o in options if _mots(o)[:1] == mots[:1]]
    return memes[0] if len(memes) == 1 else None


def _accorder(nationalite, sexe):
    """« Indien-ne » -> « Indienne » (Feminin), « Indien » (Masculin), tel
    quel sinon."""
    m = re.fullmatch(r"(.+?)-(e|ne)", nationalite.strip())
    if not m or sexe not in ("feminin", "masculin"):
        return nationalite.strip()
    return m.group(1) + (m.group(2) if sexe == "feminin" else "")


def _nir(brut):
    """NIR sans espaces : 13 chiffres (+ clé), département 2A/2B en Corse, 1er
    chiffre 7 ou 8 pour un numéro provisoire d'étranger. Une cellule numérique
    lue en notation scientifique (1.85E+14) est rejetée, pas proposée."""
    n = re.sub(r"\s", "", brut).upper()
    return n if re.fullmatch(r"[1-8]\d{4}(\d\d|2[AB])\d{6}(\d\d)?", n) else None


ALIAS_CONTRAT = {"dureeindeterminee": "CDI", "dureedeterminee": "CDD"}


def convertir(ligne):
    """-> (valeurs {id de champ: valeur}, rejets {id de champ: brut}) pour les
    champs que le formulaire du client possède."""
    champs = {c["id"]: c for c in config.champs()}
    valeurs, rejets = {}, {}

    def poser(fid, brut, conv):
        if fid in champs and brut:
            v = conv(brut)
            if v:
                valeurs[fid] = v
            else:
                rejets[fid] = brut

    def options(fid):
        return champs.get(fid, {}).get("options") or []

    sexe = "".join(_mots(ligne.get("sexe", "")))
    poser("civilite", ligne.get("sexe", ""),
          lambda s: _choix({"masculin": "Monsieur", "feminin": "Madame"}.get(sexe, ""),
                           options("civilite")))
    poser("date_naissance", _premier(ligne, "datedenaissance"), _date)
    poser("telephone", _premier(ligne, "ntelephone"), str)
    poser("num_secu", _premier(ligne, "ndesecuritesociale"), _nir)
    poser("email", _premier(ligne, "email", "emailsecondaire"), str)
    adresse = ", ".join(p for p in (
        ligne.get("adresse1", ""), ligne.get("complement", ""),
        " ".join(p for p in (ligne.get("codepostal", ""), ligne.get("ville", "")) if p)) if p)
    poser("adresse", adresse, str)

    if nat := _premier(ligne, "nationalite"):
        parts = [p.strip() for p in nat.split(",") if p.strip()] or [nat]
        if not options("nationalite"):                          # champ texte libre
            poser("nationalite", parts[0], lambda s: _accorder(s, sexe))
        elif any(sharepoint._norm(p).startswith("fran") for p in parts):
            poser("nationalite", "Français", lambda s: _choix(s, options("nationalite")))
        else:
            poser("nationalite", "Autres", lambda s: _choix(s, options("nationalite")))
            poser("nationalite_etrangere", parts[0], lambda s: _accorder(s, sexe))
    poser("type_autorisation", _premier(ligne, "nomdepermisdetravailcartedesejour"),
          lambda s: _choix(s, options("type_autorisation")))
    poser("date_fin_validite",
          _premier(ligne, "findecartedesejour", "findelautorisationdetravail"), _date)

    entree = _premier(ligne, "datedentree", "datedanciennete",
                      "datedebutdecontrat", "datededebutdecontrat")
    for fid in dict.fromkeys((config.role("date_debut"), "date_embauche")):
        poser(fid, entree, _date)
    poser("type_contrat", _premier(ligne, "typedecontrat"),
          lambda s: _choix(ALIAS_CONTRAT.get("".join(_mots(s)), s), options("type_contrat")))
    poste = config.role("poste")
    poser(poste, _premier(ligne, "poste", "intituleposte"),
          lambda s: _choix(sharepoint.poste_de(s) or s, options(poste)))

    heures = _premier(ligne, "contrathebdo", "volumehorairehebdomadaireh")
    if heures and "temps_travail" in champs:
        try:
            h = float(heures)
        except ValueError:
            h = None
        if h is not None and h == int(h) and f"{int(h)}H" in options("temps_travail"):
            valeurs["temps_travail"] = f"{int(h)}H"
            poser("temps_partiel", "OUI" if h < 35 else "NON",
                  lambda s: _choix(s, options("temps_partiel")))
        else:
            rejets["temps_travail"] = heures
    return valeurs, rejets


def par_personne(fichiers):
    """[(nom de fichier, octets)] -> {clé de nom: {nom, valeurs, conflits,
    rejets, sources}}. Toutes les lignes d'une même personne (deux exports qui
    se recouvrent, deux sections d'une synthèse) sont fusionnées : une seule valeur
    distincte est retenue, plusieurs font un conflit, jamais rempli. Un rejet
    à côté d'une valeur reconnue est aussi un conflit : les fichiers divergent."""
    vus = {}
    for nom_fichier, octets in fichiers:
        try:
            lues = lignes(octets)
        except ValueError as e:
            raise ValueError(f"{nom_fichier} : {e}") from None
        for ligne in lues:
            cle = sharepoint.cle_nom(ligne["nom"], ligne["prenom"])
            p = vus.setdefault(cle, {"nom": f"{ligne['nom']} {ligne['prenom']}",
                                     "vals": {}, "rejs": {}, "sources": set()})
            p["sources"].add(nom_fichier)
            valeurs, rejets = convertir(ligne)
            for fid, v in valeurs.items():
                p["vals"].setdefault(fid, set()).add(v)
            for fid, v in rejets.items():
                p["rejs"].setdefault(fid, set()).add(v)
    out = {}
    for cle, p in vus.items():
        vals, rejs = p["vals"], p["rejs"]
        out[cle] = {"nom": p["nom"], "sources": sorted(p["sources"]),
                    "valeurs": {f: next(iter(v)) for f, v in vals.items()
                                if len(v) == 1 and f not in rejs},
                    "conflits": {f: sorted(v | rejs.get(f, set())) for f, v in vals.items()
                                 if len(v) > 1 or f in rejs},
                    "rejets": {f: " / ".join(sorted(v)) for f, v in rejs.items()
                               if f not in vals}}
    return out
