"""Stockage : le disque est la base. Aucune DB.

Deux zones (ticket 07) :
  data/soumissions/<ulid>/soumission.json  + pieces/
  data/DOSSIERS SALARIES/<Groupe>/<Etablissement>/<Poste>/<NOM Prenom>/
      dossier.json + FICHE PERSONNELLE/ + CONTRAT/ + _versions/

Le dossier valide EST l'arborescence que la RH ouvre a la main hors de l'app.
Les clefs de bucket internes (pieces / contrat / _versions) restent le
vocabulaire du reste de l'app ; la traduction vers les libelles humains se fait
dans chemin() seul.

Le journal append-only de `<zone>.json` fait foi sur l'etat. La presence d'un
fichier est une preuve corroborante, jamais decisive.

SEAM DE STOCKAGE DES PIECES -- rien hors de ce module ne construit un chemin de
piece ni n'ouvre un fichier de bucket. Pour basculer vers S3 / SharePoint /
autre le jour ou le stockage sera decide, reimplementer ce petit jeu suffit,
sans toucher app.py ni contrat.py :
    chemin(uid, bucket, nom=None)   -- localiser  (interne : seul ce module l'appelle)
    ouvrir(uid, bucket, nom)        -- lire  -> octets | None
    deposer(uid, bucket, role, f)   -- ecrire un upload (liste blanche + archivage)
    poser_octets(uid, bucket, nom, o) -- ecrire des octets qu'on produit
    fichiers(item, bucket)          -- lister
La purge (_effacer_pieces) supprime les buckets via chemin(), efface les copies
compta (data/COMPTA/) et renomme le repertoire du dossier : un backend
S3/SharePoint doit donc aussi porter une operation de SUPPRESSION, pas seulement
lire/ecrire/lister.
Le journal (dossier.json) et son deplacement soumissions->documents (valider())
restent sur disque local quoi qu'il arrive -- c'est l'etat, pas des pieces.
"""
import hashlib
import hmac
import json
import os
import random
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

ETATS = ["Soumise", "Rejetee", "ATraiter", "ContratPret", "ContratSigne",
         "DpaeFaite", "RemisComptable", "Abandonnee"]
INACTIFS = {"Rejetee", "Abandonnee", "RemisComptable"}   # grises dans le suivi

# Transitions permises via transition() -- le point de passage unique de tous
# les appelants. Le gating vivait dans le template ; un POST direct pouvait
# ecrire une transition absurde. ContratPret a deux entrees (contrat genere ou
# contrat depose), ci-dessous cote ATraiter.
TRANSITIONS = {
    "Soumise":        {"Abandonnee"},
    "Rejetee":        {"Abandonnee"},
    "ATraiter":       {"ContratPret", "Abandonnee"},
    "ContratPret":    {"ContratSigne", "Abandonnee"},
    "ContratSigne":   {"DpaeFaite", "Abandonnee"},
    "DpaeFaite":      {"RemisComptable", "Abandonnee"},
    "RemisComptable": {"Abandonnee"},
}

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford, sans I L O U
_verrous, _garde = {}, threading.Lock()


def nouvel_id():
    """ULID : 48 bits de temps ms + 80 bits d'alea, triable par date."""
    n = (int(time.time() * 1000) << 80) | random.getrandbits(80)
    return "".join(_B32[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verrou(uid):
    """Exclusion entre threads d'UN SEUL process.

    Servi depuis un serveur multi-process, ce verrou n'exclut plus rien : deux
    read-modify-write concurrents sur le meme dossier.json (cf. transition())
    perdent une entree de journal — de la perte de donnees, pas une lenteur.

    `python app.py` demarre donc waitress, mono-process par construction. Mais
    `app` reste un objet WSGI importable : `gunicorn app:app` ou un waitress
    multi-process cassent l'invariant SANS RIEN SIGNALER. Il tient a la facon
    de lancer, pas au code. Y passer exige d'abord un verrou fichier ici
    (portalocker ; fcntl n'existe pas sous Windows) et un compteur partage pour
    les pots de rate-limit d'app.py."""
    with _garde:
        return _verrous.setdefault(uid, threading.Lock())


# --- chemins -------------------------------------------------------------

DOSSIERS = "DOSSIERS SALARIES"
COMPTA = "COMPTA"
BUCKETS = {"pieces": "FICHE PERSONNELLE", "contrat": "CONTRAT"}  # _versions non traduit
BUCKETS_COMPTA = BUCKETS                       # meme vocabulaire humain cote compta
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_ACCENTS = str.maketrans("àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ",
                         "aaaeeeeiioouuucAAAEEEEIIOOUUUC")

_carte = {}   # uid -> Path. cache process : l'app est mono-process par
              # construction (cf. _verrou) ; index disque si ca change.
_scan_items = {}   # uid -> dossier.json deja parse par _scan(), consomme par
                   # tout() dans la foulee. Pas un cache : rempli et lu dans le
                   # meme appel, jamais invalide. Divise par deux l'I/O de
                   # /suivi, qui reparse sinon chaque dossier.json (scan + lire).


def _scan():
    """Le disque fait foi : le nom du dossier ne porte plus l'ULID, mais
    dossier.json porte "id". Profondeur fixe, quelques dizaines de ms."""
    global _scan_items
    _scan_items = {}
    carte = {f.parent.name: f.parent
             for f in (config.DONNEES / "soumissions").glob("*/soumission.json")}
    for f in (config.DONNEES / DOSSIERS).glob("*/*/*/*/dossier.json"):
        item = json.loads(f.read_text(encoding="utf-8"))
        carte[item["id"]] = f.parent
        _scan_items[item["id"]] = item
    return carte


def dossier_de(uid):
    """Le repertoire de l'unite, quelle que soit sa zone. None si inconnue."""
    global _carte
    d = _carte.get(uid)
    if d and d.is_dir():      # is_dir, PAS le json : creer_soumission depose des
        return d              # pieces avant d'ecrire soumission.json
    _carte = _scan()          # miss, ou dossier deplace a la main par la RH
    return _carte.get(uid)


def _soumission(d):
    return d.parent == config.DONNEES / "soumissions"


def _fichier_json(d):
    return d / ("soumission.json" if _soumission(d) else "dossier.json")


def _rep(item):
    """Le repertoire d'un item DEJA lu ; lire() l'a pose dans _dir. Le rebatir
    par zone/uid n'est plus possible."""
    return Path(item["_dir"])


def chemin(uid, bucket, nom=None):
    """Repertoire d'un bucket de pieces (pieces / contrat / _versions), ou un
    fichier nomme dedans. None si le dossier est inconnu.

    LE seul endroit hors de ce module ou l'agencement <dossier>/<bucket>/<nom>
    est construit -- avec deposer() et poser_octets(). Sortir les pieces vers un
    SharePoint ou un stockage objet se joue ici, sans toucher a app.py."""
    d = dossier_de(uid)
    if not d:
        return None
    d = d / BUCKETS.get(bucket, bucket)
    return d / nom if nom else d


# --- lecture / ecriture --------------------------------------------------

def lire(uid):
    d = dossier_de(uid)
    if not d:
        return None
    item = json.loads(_fichier_json(d).read_text(encoding="utf-8"))
    item["_dir"] = str(d)
    # etiquette logique desormais, plus un nom de repertoire.
    item["_zone"] = "soumissions" if _soumission(d) else "documents"
    return item


def _ecrire(d, item):
    """temp-puis-rename : jamais de JSON a moitie ecrit."""
    item = {k: v for k, v in item.items() if not k.startswith("_")}
    f = _fichier_json(d)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def etat(item):
    return item["journal"][-1]["vers"]


def tout():
    """Collection complete, soumissions + dossiers, la plus recente d'abord."""
    global _carte
    _carte = _scan()
    items = []
    for uid, d in _carte.items():
        item = _scan_items.get(uid)               # dossier.json deja parse au scan
        if item is None:                          # soumission : json non lu au scan
            item = json.loads(_fichier_json(d).read_text(encoding="utf-8"))
        item["_dir"] = str(d)
        item["_zone"] = "soumissions" if _soumission(d) else "documents"
        items.append(item)
    return sorted(items, key=lambda i: config.valeur(i["champs"], "date_debut") or "9999",
                  reverse=False)


# --- pieces --------------------------------------------------------------

SAIN = re.compile(r"[^A-Za-z0-9._-]+")
EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
TAILLE_MAX = 15 * 1024 * 1024


def deposer(uid, bucket, role, fichier, extensions=EXTENSIONS, index=None):
    """Ecrit une piece sous un nom semantique. Un re-depot archive l'ancien.

    `extensions` surcharge la liste blanche par defaut : le contrat venu de
    myrhis est un PDF ou un .docx, mais on n'elargit PAS EXTENSIONS, qui
    protege le formulaire public.

    `index` (optionnel) : rang 1..N pour les champs multi-fichiers. Le 1er
    fichier garde le nom nu, les suivants prennent un suffixe `_2`, `_3`...

    Nom du fichier : technique (`{role}.{ext}`) tant que le dossier est en
    soumission ; lisible (`<Libellé> - NOM Prénom.ext`, cf. nom_piece) une fois
    validé -- c'est le dossier que la RH ouvre a la main.
    """
    d = dossier_de(uid)
    if not d:
        raise ValueError(f"dossier inconnu : {uid}")
    ext = os.path.splitext(fichier.filename or "")[1].lower()
    if ext not in extensions:
        raise ValueError(f"format refuse ({ext or 'sans extension'}) : "
                         + ", ".join(sorted(extensions)))
    if _soumission(d):
        nom_role = SAIN.sub("-", role)
        if index is not None and index > 1:
            nom_role = f"{nom_role}_{index}"
        cible = chemin(uid, bucket, nom_role + ext)
    else:
        item = json.loads(_fichier_json(d).read_text(encoding="utf-8"))
        cible = chemin(uid, bucket, nom_piece(item, SAIN.sub("-", role), ext, index))
        # Dossier valide d'avant le renommage : le fichier de meme role est encore
        # au nom court. cible.exists() (plus bas) ne le verrait pas -> il
        # cohabiterait avec le nom lisible. On l'archive ici, comme un re-depot.
        for vieux in fichiers_role(item, bucket, role):
            if vieux != cible.name:
                p = chemin(uid, bucket, vieux)
                arch = chemin(uid, "_versions", f"{p.stem}-{int(time.time())}{p.suffix}")
                arch.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(arch))
    cible.parent.mkdir(parents=True, exist_ok=True)
    # Valider le fichier entrant AVANT de deplacer l'ancien : sinon un re-depot
    # refuse (trop lourd) laisse le dossier sans piece et l'ancienne copie
    # valide echouee dans _versions.
    tmp = cible.with_name(cible.name + ".entrant")
    fichier.save(str(tmp))
    if tmp.stat().st_size > TAILLE_MAX:
        tmp.unlink()
        raise ValueError(f"fichier trop lourd (max {TAILLE_MAX // 1024 // 1024} Mo)")
    if cible.exists():
        vers = chemin(uid, "_versions", f"{cible.stem}-{int(time.time())}{ext}")
        vers.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cible), str(vers))
    os.replace(tmp, cible)
    return cible.name


def poser_octets(uid, bucket, nom, octets):
    """Ecrit des octets qu'on produit nous-memes (PDF revenu de l'e-signature).
    Atomique, sans validation d'upload : la source n'est pas le formulaire
    public. -> le nom de fichier ecrit."""
    cible = chemin(uid, bucket, nom)
    if not cible:
        raise ValueError(f"dossier inconnu : {uid}")
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_suffix(cible.suffix + ".tmp")
    tmp.write_bytes(octets)
    os.replace(tmp, cible)
    return cible.name


def ouvrir(uid, bucket, nom):
    """Le contenu d'une piece (octets), ou None si absente. Point de lecture
    unique -- cf. le SEAM en tete de module."""
    p = chemin(uid, bucket, nom)
    return p.read_bytes() if p and p.is_file() else None


def fichiers(item, bucket):
    d = chemin(item["id"], bucket)
    return sorted(f.name for f in d.glob("*") if f.is_file()) if d and d.is_dir() else []


# Libelle lisible d'un role de piece. CONTRAINTE : chaque valeur doit se
# re-sluguer vers sa cle -- SAIN.sub("-", sans_accent(v)).strip("-").lower() == k
# --, sinon _extraire_role() ne retrouve plus le role depuis un nom lisible.
# D'ou "Identite" et pas "Piece d'identite".
LIBELLES_PIECE = {"contrat": "Contrat", "contrat-signe": "Contrat signé",
                  "accuse-dpae": "Accusé DPAE", "fiche-salarie": "Fiche salarié",
                  "identite": "Identité", "carte-vitale": "Carte vitale",
                  "rib": "RIB"}


def date_remise(item):
    """Jour (AAAA-MM-JJ) de la derniere remise/renvoi au comptable ; repli aujourd'hui.
    Fige la date des noms de fichiers : un cabinet qui telecharge le lot une
    semaine plus tard garde la date de la remise, pas celle du clic."""
    jours = [e["le"][:10] for e in item["journal"]
             if e.get("vers") == "RemisComptable" or e.get("type") == "renvoi_comptable"]
    return jours[-1] if jours else datetime.now(timezone.utc).date().isoformat()


def _qui(item):
    prenom, nom_s = config.identite(item["champs"])
    return " ".join(p for p in (nom_s.upper(), prenom) if p) or "SANS NOM"


def _tete(nom):
    """Le mot de tete d'un nom de piece : avant l'extension ET avant ' - '.
    'identite_2.pdf' -> 'identite_2' ; 'Identité_2 - MARTIN Camille.pdf' -> 'Identité_2'"""
    return nom.rsplit(".", 1)[0].split(" - ", 1)[0]


def _index_nom(nom):
    """Rang multi-fichiers ('identite_2.pdf' / 'Identité_2 - X.pdf') -> 2, sinon None."""
    m = re.search(r"_(\d+)$", _tete(nom))
    return int(m.group(1)) if m else None


def nom_piece(item, role, ext, index=None):
    """Nom lisible d'une piece DANS LE DOSSIER VALIDE : '<Libellé> - NOM Prénom.ext'.
    Sans date (choix : la RH ouvre ce dossier a la main, la date encombre).
    `ext` avec le point. cf. nom_export pour la variante compta (datee, MAJ)."""
    libelle = LIBELLES_PIECE.get(role, role)
    suff = f"_{index}" if index and index > 1 else ""
    return f"{libelle}{suff} - {_qui(item)}{ext}"


def nom_export(item, bucket, nom):
    """Nom lisible d'une piece pour la compta et le lot :
    '<libelle> - NOM Prenom - AAAA-MM-JJ.ext'. bucket ignore (role deja dans nom)."""
    ext = os.path.splitext(nom)[1]
    role = _extraire_role(nom)                      # tous regimes de nom
    libelle = LIBELLES_PIECE.get(role, role.replace("-", " "))
    idx = _index_nom(nom)
    suff = f"_{idx}" if idx and idx > 1 else ""
    return f"{libelle}{suff} - {_qui(item)} - {date_remise(item)}{ext}"


def copier_compta(item):
    """Duplique les pieces dans data/COMPTA/<GROUPE>/<ETABLISSEMENT>/<POSTE>/
    <NOM PRENOM - id>/{FICHE PERSONNELLE,CONTRAT}/, le dossier que le cabinet
    recupere (miroite sur un Drive au besoin). Segments en capitales sans accents,
    fichiers renommes (cf. nom_export). Rejouable : un renvoi ecrase la copie
    precedente. -> le repertoire."""
    groupe, etab, poste, qui = _hierarchie(item)
    dest = (config.DONNEES / COMPTA
            / _segment_maj(groupe, "DIVERS")
            / _segment_maj(etab, "SANS ETABLISSEMENT")
            / _segment_maj(poste, "SANS POSTE")
            / f'{_segment_maj(qui, item["id"])} - {item["id"]}')
    for bucket in ("pieces", "contrat"):           # jamais _versions
        sous = dest / BUCKETS_COMPTA[bucket]
        sous.mkdir(parents=True, exist_ok=True)
        for nom_f in fichiers(item, bucket):
            (sous / nom_export(item, bucket, nom_f)).write_bytes(
                ouvrir(item["id"], bucket, nom_f))
    return dest


def _extraire_role(nom_fichier):
    """Role d'un nom de piece, quel que soit le regime :
       'identite.pdf', 'carte-vitale_2.jpg'        -> nom technique (soumission, vieux dossiers)
       'Identité - MARTIN Camille.pdf'             -> nom lisible (dossier valide, cf. nom_piece)
       'Carte vitale_2 - MARTIN Camille.jpg'       -> lisible + multi-fichiers
    Le mot de tete est re-slugue : c'est pourquoi les valeurs de LIBELLES_PIECE
    doivent round-tripper (cf. la contrainte au-dessus du dict)."""
    tete = re.sub(r"_\d+$", "", _tete(nom_fichier))
    return SAIN.sub("-", tete.translate(_ACCENTS)).strip("-").lower()


def fichiers_role(item, bucket, role):
    """Liste des noms de fichiers pour un role donne (indexe ou non)."""
    role_propre = SAIN.sub("-", role)
    return [f for f in fichiers(item, bucket) if _extraire_role(f) == role_propre]


def manquantes(item):
    """Pieces requises par le schema et absentes du disque.

    Retourne une liste de dicts {'champ': config}.  `max_fichiers` est un
    plafond, pas un minimum : un role avec au moins un fichier est satisfait.
    Compatible bool : [] == tout est la.
    """
    # Un dossier purge n'a plus ses pieces (effacees expres) : sans ce garde,
    # rmtree ferait repasser toutes les pieces requises en « manquantes » et
    # /suivi afficherait un rouge permanent et faux. Couvre les quatre appelants
    # (suivi, detail, _saisie, _fiche_salarie).
    if item.get("purge"):
        return []
    presents = {_extraire_role(f) for f in fichiers(item, "pieces")}
    return [{"champ": c} for c in config.pieces()
            if c.get("requis") and SAIN.sub("-", c["role"]) not in presents]


# --- cycle de vie --------------------------------------------------------

def _deposer_champ(uid, c, fichiers_recus):
    """Depose le(s) fichier(s) recu(s) pour un champ piece_jointe.

    `fichiers_recus` peut etre un MultiDict (request.files) ou un simple dict
    (import myrhis, tests). Le multi-fichiers n'est possible que sur le premier.
    """
    if c.get("max_fichiers", 1) > 1 and hasattr(fichiers_recus, "getlist"):
        recus = fichiers_recus.getlist(c["id"])
    else:
        f = fichiers_recus.get(c["id"])
        recus = [f] if f else []
    rang = 0
    for f in recus:
        if f and f.filename:
            rang += 1
            deposer(uid, "pieces", c["role"], f, index=rang)


def _segment(brut, defaut):
    """Un composant de chemin sur : illegaux Windows remplaces, points/espaces
    de fin otes (NTFS les refuse), 60 car. max (MAX_PATH)."""
    s = _ILLEGAL.sub("-", str(brut or "")).strip().rstrip(". ")[:60].strip()
    return s or defaut


def _segment_maj(brut, defaut):
    """Comme _segment, mais capitales sans accents -- l'arbo COMPTA seulement."""
    return _segment(str(brut or "").translate(_ACCENTS).upper(), defaut)


def _hierarchie(item):
    """(groupe, etablissement, poste, "NOM Prenom") brut, non segmente.
    Commun a _dossier_cible (DOSSIERS SALARIES) et copier_compta (COMPTA)."""
    ch = item["champs"]
    cle = config.valeur(ch, "etablissement")
    try:
        groupe, etab = config.groupe(cle), config.etablissement(cle)[1]["nom"]
    except KeyError:                       # etablissement retire de la config
        groupe, etab = "DIVERS", cle
    prenom, nom = config.identite(ch)
    qui = " ".join(p for p in (nom.upper(), prenom) if p) or item["id"]
    return groupe, etab, config.valeur(ch, "poste"), qui


def _dossier_cible(item):
    """Calcule UNE FOIS, a la validation : renommer un etablissement en config
    ne deplace donc jamais un dossier deja cree."""
    groupe, etab, poste, qui = _hierarchie(item)
    parent = (config.DONNEES / DOSSIERS / _segment(groupe, "DIVERS")
              / _segment(etab, "SANS ETABLISSEMENT")
              / _segment(poste, "SANS POSTE"))
    nom_d = _segment(qui, item["id"])
    cible = parent / nom_d
    # Homonyme : sans ce suffixe, shutil.move fusionnerait deux salaries.
    return cible if not cible.exists() else parent / f"{nom_d} ({item['id'][-4:]})"


def creer_soumission(champs, fichiers_recus):
    uid = nouvel_id()
    d = config.DONNEES / "soumissions" / uid
    d.mkdir(parents=True)
    _carte[uid] = d              # les pieces partent avant soumission.json
    item = {"schema_version": config.formulaire()["version"], "id": uid,
            "champs": champs, "link_epoch": 0,
            "journal": [{"de": None, "vers": "Soumise", "le": maintenant(),
                         "par": "formulaire"}]}
    try:
        for c in config.pieces():
            _deposer_champ(uid, c, fichiers_recus)
    except ValueError:              # piece refusee : pas de dossier a moitie ne
        shutil.rmtree(d, ignore_errors=True)
        raise
    _ecrire(d, item)
    return uid


def resoumettre(uid, champs, fichiers_recus):
    """Correction apres KO : la MEME soumission repasse a Soumise."""
    with _verrou(uid):
        item = lire(uid)
        d = _rep(item)
        item["champs"] = champs
        for c in config.pieces():
            _deposer_champ(uid, c, fichiers_recus)
        item["journal"].append({"de": etat(item), "vers": "Soumise",
                                "le": maintenant(), "par": "formulaire",
                                "motif": "correction"})
        _ecrire(d, item)


def valider(uid, par):
    """OK de la RH : la soumission devient un dossier salarie (meme ULID)."""
    with _verrou(uid):
        item = lire(uid)
        if item["_zone"] != "soumissions":
            raise ValueError("deja un dossier salarie")
        src = _rep(item)
        dst = _dossier_cible(item)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _carte[uid] = dst
        (dst / "soumission.json").unlink(missing_ok=True)
        # Le dossier valide = l'arbo que la RH ouvre a la main : les pieces du
        # candidat (noms techniques) passent en noms lisibles, comme tout ce qui
        # sera depose/produit ensuite. Idempotent (un nom deja lisible se re-rend
        # identique). Les vieux dossiers deja valides ne sont pas balayes.
        pdir = dst / BUCKETS["pieces"]
        for f in sorted(pdir.glob("*")) if pdir.is_dir() else []:
            if f.is_file():
                voulu = f.with_name(nom_piece(item, _extraire_role(f.name),
                                              f.suffix, _index_nom(f.name)))
                if voulu != f and not voulu.exists():
                    f.rename(voulu)
        item["lien_comptable_epoch"] = 0
        item["journal"].append({"de": "Soumise", "vers": "ATraiter",
                                "le": maintenant(), "par": par})
        _ecrire(dst, item)


def rejeter(uid, motif, commentaire, par):
    with _verrou(uid):
        item = lire(uid)
        d = _rep(item)
        item["link_epoch"] = item.get("link_epoch", 0) + 1   # tue l'ancien lien
        item["journal"].append({"de": etat(item), "vers": "Rejetee",
                                "le": maintenant(), "par": par,
                                "motif": motif, "commentaire": commentaire})
        _ecrire(d, item)


def transition(uid, vers, par, **extra):
    with _verrou(uid):
        item = lire(uid)
        de = etat(item)
        if vers not in TRANSITIONS.get(de, set()):
            raise ValueError(f"transition interdite : {de} -> {vers}")
        d = _rep(item)
        item["journal"].append({"de": de, "vers": vers,
                                "le": maintenant(), "par": par, **extra})
        _ecrire(d, item)
        return item


def completer_champs(uid, nouveaux):
    """Fusionne des valeurs saisies apres la soumission (saisie RH a la
    generation du contrat) dans item['champs']. Idempotent, ne journalise pas."""
    with _verrou(uid):
        item = lire(uid)
        d = _rep(item)
        item["champs"] = {**item["champs"], **{k: v for k, v in nouveaux.items() if v != ""}}
        _ecrire(d, item)
        return item


def noter(uid, **entree):
    """Entree de journal sans changement d'etat (acces au lot, mail parti...)."""
    with _verrou(uid):
        item = lire(uid)
        d = _rep(item)
        item["journal"].append({"de": etat(item), "vers": etat(item),
                                "le": maintenant(), **entree})
        _ecrire(d, item)


def bump_epoch(uid, cle):
    with _verrou(uid):
        item = lire(uid)
        d = _rep(item)
        item[cle] = item.get(cle, 0) + 1
        _ecrire(d, item)
        return item[cle]


# --- conservation / purge ----------------------------------------------
#
# La purge EFFACE les pieces et garde une trace dans le journal. Elle vit dans
# une requete de l'app (bouton RH), jamais dans un cron : un second process
# ecrivain casserait l'invariant mono-process (cf. _verrou). purger.py ne fait
# que LIRE et lister.

_CHAMPS_GARDES = ("etablissement", "poste", "date_debut")   # roles que /suivi lit
_JOURNAL_GARDE = ("le", "de", "vers", "par", "type")


def _entree_etat_courant(item):
    """Date de la derniere entree qui a fait ENTRER dans l'etat courant.

    PAS journal[-1]["le"] : le journal porte aussi acces_lot, mail_echoue,
    renvoi_comptable -- un dossier remis il y a 3 ans dont le cabinet a ouvert
    le lien il y a 2 ans ne serait jamais purge. L'entree de purge elle-meme
    (de == vers) est ignoree par le meme critere."""
    courant = etat(item)
    for e in reversed(item["journal"]):
        if e.get("vers") == courant and e.get("de") != e.get("vers"):
            return e["le"]
    return item["journal"][0]["le"]


def _eligibilite(item, conf):
    """(jours_ecoules, duree) si `item` est eligible a la purge MAINTENANT,
    sinon None. Eligible = pas deja purge, etat courant dans conf["apres"], et
    entre dans cet etat depuis au moins `duree` jours (`jours_candidature` si
    l'etat est « Soumise », `jours` sinon).

    Un dossier deja purge (cle `purge`) est exclu -- jamais par la date :
    l'entree de purge repousserait l'echeance et un dossier purge a moitie ne
    serait plus jamais reexamine.

    Partage entre eligibles() et purger() : ce dernier revérifie pour de vrai,
    la liste affichee a l'ecran de confirmation a pu vieillir entre le GET et
    le POST (un renvoi / abandon a pu bouger la date d'entree dans l'etat)."""
    if item.get("purge") or etat(item) not in (conf.get("apres") or []):
        return None
    duree = (conf.get("jours_candidature", conf["jours"])
             if etat(item) == "Soumise" else conf["jours"])
    ecoule = (datetime.now(timezone.utc)
              - datetime.fromisoformat(_entree_etat_courant(item))).days
    return (ecoule, duree) if ecoule >= duree else None


def eligibles(items=None):
    """[(item, jours_ecoules, duree)] des dossiers purgables. `items` :
    collection deja scannee (/suivi la passe pour ne pas rescanner)."""
    conf = config.conservation()
    if not conf.get("apres"):
        return []
    out = []
    for item in (tout() if items is None else items):
        if e := _eligibilite(item, conf):
            out.append((item, e[0], e[1]))
    return out


def _journal_nettoye(journal):
    """Squelette de chaque entree : jette `commentaire` (texte libre saisi par
    la RH), `ip` des acces_lot, `procedure` Yousign."""
    return [{k: e[k] for k in _JOURNAL_GARDE if k in e} for e in journal]


def _effacer_pieces(uid, item, d):
    """Effacements idempotents : chacun encaisse son OSError sans arreter les
    suivants. Le renommage du repertoire vient EN DERNIER -- c'est l'operation
    la plus susceptible d'echouer (Explorateur ouvert, OneDrive en upload ->
    WinError 32) ; un echec ne laisse alors que le nom, rejouable."""
    sur_soumission = _soumission(d)
    for nom in ("dossier.tmp", "soumission.tmp"):
        try:
            (d / nom).unlink()
        except OSError:
            pass
    for bucket in ("pieces", "contrat", "_versions"):
        cible = chemin(uid, bucket)            # traduction des libelles via le seam
        if cible:
            shutil.rmtree(cible, ignore_errors=True)
    # Zone documents seulement : TOUTES les copies compta (un renommage de
    # societe entre remettre et renvoyer en cree deux). compta/ n'est pas un
    # bucket de dossier -- meme chemin construit en dur que copier_compta().
    if not sur_soumission:
        for copie in (config.DONNEES / COMPTA).glob(f"**/* - {uid}"):
            shutil.rmtree(copie, ignore_errors=True)
    # Zone soumissions : le repertoire est nomme par l'ULID (aucune PII) et
    # _soumission() teste le parent -- ne rien renommer ni supprimer.
    if sur_soumission or d.name == f"purge-{uid}":
        return
    # Le nom du repertoire EST une donnee personnelle (« NOM Prenom »).
    try:
        cible = d.parent / f"purge-{uid}"
        d.rename(cible)
        _carte[uid] = cible
    except OSError:
        pass


def purger(uid):
    """Efface les pieces d'un dossier eligible, garde le journal. Rejouable :
    un second passage ne leve pas et ne change rien.

    Marqueur `purge` ecrit AVANT tout effacement -- c'est le SEUL point de
    reprise : s'il est deja la, on reprend directement aux effacements. Sinon
    on REVÉRIFIE l'eligibilite (l'ecran de confirmation a pu vieillir) et on
    sort sans rien toucher si le dossier n'y est plus."""
    with _verrou(uid):
        item = lire(uid)
        if not item:
            return
        d = _rep(item)
        if not item.get("purge"):
            elig = _eligibilite(item, config.conservation())
            if not elig:
                return
            courant = etat(item)
            item["champs"] = {config.role(r): config.valeur(item["champs"], r)
                              for r in _CHAMPS_GARDES
                              if config.valeur(item["champs"], r)}
            item["journal"] = _journal_nettoye(item["journal"]) + [
                {"de": courant, "vers": courant, "le": maintenant(),
                 "par": "systeme", "type": "purge"}]
            # sinon un jeton de moins de 30 j reste valide sur un dossier vide.
            item["link_epoch"] = item.get("link_epoch", 0) + 1
            item["lien_comptable_epoch"] = item.get("lien_comptable_epoch", 0) + 1
            item["purge"] = {"le": maintenant(), "jours": elig[1]}
            _ecrire(d, item)
        _effacer_pieces(uid, item, d)


# --- liens signes (ticket 08/09) -----------------------------------------

def signer(but, uid, epoch):
    charge = f"{but}.{uid}.{int(time.time())}.{epoch}"
    sig = hmac.new(config.secret(), charge.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{charge}.{sig}"


def verifier_lien(jeton, but, jours=30):
    """-> item, ou None si signature invalide, expiree ou revoquee par epoch."""
    try:
        b, uid, emis, epoch, sig = jeton.split(".")
    except ValueError:
        return None
    attendu = hmac.new(config.secret(), f"{b}.{uid}.{emis}.{epoch}".encode(),
                       hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, attendu) or b != but:
        return None
    if datetime.fromtimestamp(int(emis), timezone.utc) + timedelta(days=jours) < \
            datetime.now(timezone.utc):
        return None
    item = lire(uid)
    cle = "link_epoch" if but == "correction" else "lien_comptable_epoch"
    if not item or str(item.get(cle, 0)) != epoch:
        return None
    return item
