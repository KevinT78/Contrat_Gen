"""Stockage : le disque est la base. Aucune DB.

Deux zones (ticket 07) :
  data/soumissions/<ulid>/soumission.json  + pieces/
  data/documents/<ulid>/dossier.json       + pieces/ + contrat/

Le journal append-only de `<zone>.json` fait foi sur l'etat. La presence d'un
fichier est une preuve corroborante, jamais decisive.
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

import config

ETATS = ["Soumise", "Rejetee", "ATraiter", "ContratPret", "ContratSigne",
         "RappelDpae", "DpaeFaite", "RemisComptable", "Abandonnee"]
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
    "ContratSigne":   {"RappelDpae", "DpaeFaite", "Abandonnee"},
    "RappelDpae":     {"DpaeFaite", "Abandonnee"},
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

def dossier_de(uid):
    """Le repertoire de l'unite, quelle que soit sa zone. None si inconnue."""
    for zone in ("documents", "soumissions"):
        d = config.DONNEES / zone / uid
        if d.is_dir():
            return d
    return None


def _fichier_json(d):
    return d / ("dossier.json" if d.parent.name == "documents" else "soumission.json")


# --- lecture / ecriture --------------------------------------------------

def lire(uid):
    d = dossier_de(uid)
    if not d:
        return None
    item = json.loads(_fichier_json(d).read_text(encoding="utf-8"))
    item["_dir"] = str(d)
    item["_zone"] = d.parent.name
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
    items = []
    for zone in ("soumissions", "documents"):
        for d in sorted((config.DONNEES / zone).glob("*/")):
            if _fichier_json(d).exists():
                items.append(lire(d.name))
    return sorted(items, key=lambda i: config.valeur(i["champs"], "date_debut") or "9999",
                  reverse=False)


# --- pieces --------------------------------------------------------------

SAIN = re.compile(r"[^A-Za-z0-9._-]+")
EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
TAILLE_MAX = 15 * 1024 * 1024


def deposer(d, bucket, role, fichier, extensions=EXTENSIONS):
    """Ecrit une piece sous un nom semantique. Un re-depot archive l'ancien.

    `extensions` surcharge la liste blanche par defaut : le contrat venu de
    myrhis est un PDF ou un .docx, mais on n'elargit PAS EXTENSIONS, qui
    protege le formulaire public.
    """
    ext = os.path.splitext(fichier.filename or "")[1].lower()
    if ext not in extensions:
        raise ValueError(f"format refuse ({ext or 'sans extension'}) : "
                         + ", ".join(sorted(extensions)))
    cible = d / bucket / (SAIN.sub("-", role) + ext)
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
        vers = d / "_versions" / f"{cible.stem}-{int(time.time())}{ext}"
        vers.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cible), str(vers))
    os.replace(tmp, cible)
    return cible.name


def fichiers(item, bucket):
    d = config.DONNEES / item["_zone"] / item["id"] / bucket
    return sorted(f.name for f in d.glob("*") if f.is_file()) if d.is_dir() else []


def manquantes(item):
    """Roles de pieces requis par le schema et absents du disque."""
    presents = {f.rsplit(".", 1)[0] for f in fichiers(item, "pieces")}
    return [c for c in config.pieces()
            if c.get("requis") and SAIN.sub("-", c["role"]) not in presents]


# --- cycle de vie --------------------------------------------------------

def creer_soumission(champs, fichiers_recus):
    uid = nouvel_id()
    d = config.DONNEES / "soumissions" / uid
    d.mkdir(parents=True)
    item = {"schema_version": config.formulaire()["version"], "id": uid,
            "champs": champs, "link_epoch": 0,
            "journal": [{"de": None, "vers": "Soumise", "le": maintenant(),
                         "par": "formulaire"}]}
    for c in config.pieces():
        f = fichiers_recus.get(c["id"])
        if f and f.filename:
            deposer(d, "pieces", c["role"], f)
    _ecrire(d, item)
    return uid


def resoumettre(uid, champs, fichiers_recus):
    """Correction apres KO : la MEME soumission repasse a Soumise."""
    with _verrou(uid):
        item = lire(uid)
        d = config.DONNEES / "soumissions" / uid
        item["champs"] = champs
        for c in config.pieces():
            f = fichiers_recus.get(c["id"])
            if f and f.filename:
                deposer(d, "pieces", c["role"], f)
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
        src = config.DONNEES / "soumissions" / uid
        dst = config.DONNEES / "documents" / uid
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        (dst / "soumission.json").unlink(missing_ok=True)
        item["lien_comptable_epoch"] = 0
        item["journal"].append({"de": "Soumise", "vers": "ATraiter",
                                "le": maintenant(), "par": par})
        _ecrire(dst, item)


def rejeter(uid, motif, commentaire, par):
    with _verrou(uid):
        item = lire(uid)
        d = config.DONNEES / item["_zone"] / uid
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
        d = config.DONNEES / item["_zone"] / uid
        item["journal"].append({"de": de, "vers": vers,
                                "le": maintenant(), "par": par, **extra})
        _ecrire(d, item)
        return item


def completer_champs(uid, nouveaux):
    """Fusionne des valeurs saisies apres la soumission (saisie RH a la
    generation du contrat) dans item['champs']. Idempotent, ne journalise pas."""
    with _verrou(uid):
        item = lire(uid)
        d = config.DONNEES / item["_zone"] / uid
        item["champs"] = {**item["champs"], **{k: v for k, v in nouveaux.items() if v != ""}}
        _ecrire(d, item)
        return item


def noter(uid, **entree):
    """Entree de journal sans changement d'etat (acces au lot, mail parti...)."""
    with _verrou(uid):
        item = lire(uid)
        d = config.DONNEES / item["_zone"] / uid
        item["journal"].append({"de": etat(item), "vers": etat(item),
                                "le": maintenant(), **entree})
        _ecrire(d, item)


def bump_epoch(uid, cle):
    with _verrou(uid):
        item = lire(uid)
        d = config.DONNEES / item["_zone"] / uid
        item[cle] = item.get(cle, 0) + 1
        _ecrire(d, item)
        return item[cle]


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
