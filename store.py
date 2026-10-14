"""Stockage : MongoDB. Aucune donnee salarie sur le disque.

Deux collections :
  dossiers  un document par soumission/dossier, `_id` = ULID.
            {zone: soumissions|documents, etat, champs, journal, link_epoch, ...}
  fichiers  un document par piece, octets inline (TAILLE_MAX 15 Mo < 16 Mo BSON).
            {uid, bucket: pieces|contrat|_versions, nom, octets, le}

Le journal append-only fait foi sur l'etat : `etat(item)` le lit toujours.
Le champ `etat` du document n'est qu'un cache de journal[-1].vers, ecrit dans la
meme mise a jour atomique, qui sert de filtre de compare-and-swap
(cf. _journaliser) : deux ecrivains concurrents, meme dans deux process, ne
perdent plus d'entree de journal.

lire()/tout() rendent le document avec `id` (au lieu de `_id`) et `_fichiers`
({bucket: [noms]}). Les pieces ne se lisent et ne s'ecrivent que par ce module :
ouvrir, deposer, poser_octets, fichiers.

Base : MONGO_URI + MONGO_DB (env, sans defaut). MONGO_URI=mongomock:// donne une
base en memoire propre au process (tests).
"""
import hashlib
import hmac
import json
import os
import random
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

import config

ETATS = ["Soumise", "Rejetee", "ATraiter", "ContratPret",
         "DpaeFaite", "RemisComptable", "Abandonnee", "Parti"]
INACTIFS = {"Rejetee", "Abandonnee", "RemisComptable", "Parti"}   # grises dans le suivi, sans frise sur la fiche

# Transitions permises via transition() -- le point de passage unique de tous
# les appelants. Le gating vivait dans le template ; un POST direct pouvait
# ecrire une transition absurde. ContratPret a deux entrees (contrat genere ou
# contrat depose), ci-dessous cote ATraiter.
TRANSITIONS = {
    "Soumise":        {"Abandonnee"},
    "Rejetee":        {"Abandonnee"},
    "ATraiter":       {"ContratPret", "Abandonnee"},
    "ContratPret":    {"DpaeFaite", "Abandonnee"},
    # etat herite : dossiers ecrits avant la suppression de l'etape, jamais atteint
    "ContratSigne":   {"DpaeFaite", "Abandonnee"},
    "DpaeFaite":      {"RemisComptable", "Abandonnee"},
    "RemisComptable": {"Parti"},
    "Parti":          set(),                  # vraiment terminal
}

# Etats terminaux "procedure allee au bout" -- remplace les comparaisons en
# dur a "RemisComptable" (suivi/salaries doivent aussi savoir masquer Parti).
TERMINES = {"RemisComptable", "Parti"}

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford, sans I L O U


def nouvel_id():
    """ULID : 48 bits de temps ms + 80 bits d'alea, triable par date."""
    n = (int(time.time() * 1000) << 80) | random.getrandbits(80)
    return "".join(_B32[(n >> (5 * i)) & 31] for i in range(25, -1, -1))


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- base ----------------------------------------------------------------

_db = None
_init = threading.Lock()   # deux threads au premier appel ouvriraient deux bases mongomock


def db():
    global _db
    with _init:
        if _db is None:
            uri, nom = os.environ.get("MONGO_URI"), os.environ.get("MONGO_DB")
            if not uri or not nom:
                raise RuntimeError("base mongo non configurée : MONGO_URI et MONGO_DB "
                                   "sont requis dans l'environnement")
            if uri.startswith("mongomock://"):
                import mongomock
                client = mongomock.MongoClient()
            else:
                client = MongoClient(uri)
            base = client[nom]
            if uri.startswith("mongomock://") and os.environ.get("MONGOMOCK_AMORCE"):
                # Une base mongomock est propre a son process : un sous-process
                # (serveur de dev, test) y retrouve ses comptes par ce fichier
                # JSON {collection: [docs]}. Branche mongomock seulement : en
                # prod, mongomock n'est pas installe et cette variable ne sert a rien.
                for col, docs in json.loads(Path(os.environ["MONGOMOCK_AMORCE"])
                                            .read_text(encoding="utf-8")).items():
                    if docs:          # insert_many refuse une liste vide
                        base[col].insert_many(docs)
            base.dossiers.create_index("import_cle", unique=True, sparse=True)
            base.fichiers.create_index([("uid", 1), ("bucket", 1)])
            base.societes.create_index("nom", unique=True)
            _db = base
    return _db


def poser_compte(ident, mdp):
    """Cree ou remplace un compte de connexion (collection `comptes`)."""
    from werkzeug.security import generate_password_hash
    db().comptes.replace_one({"_id": ident}, {"mdp_hash": generate_password_hash(mdp)},
                             upsert=True)


# Racines de l'export zip (lot 4) : l'arborescence que la RH ouvre a la main.
DOSSIERS = "DOSSIERS SALARIES"
LEAVERS = "LEAVERS"
BUCKETS = {"pieces": "FICHE PERSONNELLE", "contrat": "CONTRAT"}  # _versions non traduit
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_ACCENTS = str.maketrans("àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ",
                         "aaaeeeeiioouuucAAAEEEEIIOOUUUC")


# --- lecture --------------------------------------------------------------

def _noms(filtre):
    """{uid: {bucket: [noms tries]}} des fichiers qui matchent, sans les octets."""
    out = {}
    for f in db().fichiers.find(filtre, {"uid": 1, "bucket": 1, "nom": 1}):
        out.setdefault(f["uid"], {}).setdefault(f["bucket"], set()).add(f["nom"])
    return {u: {b: sorted(n) for b, n in bs.items()} for u, bs in out.items()}


def _vue(doc, noms):
    item = dict(doc)
    item["id"] = item.pop("_id")
    item["_fichiers"] = noms
    return item


def lire(uid):
    doc = db().dossiers.find_one({"_id": uid})
    if not doc:
        return None
    return _vue(doc, _noms({"uid": uid}).get(uid, {}))


def etat(item):
    return item["journal"][-1]["vers"]


def tout():
    """Collection complete, soumissions + dossiers, la plus recente d'abord."""
    noms = _noms({})
    items = [_vue(d, noms.get(d["_id"], {})) for d in db().dossiers.find()]
    return sorted(items, key=lambda i: config.valeur(i["champs"], "date_debut") or "9999",
                  reverse=False)


# --- pieces --------------------------------------------------------------

SAIN = re.compile(r"[^A-Za-z0-9._-]+")
EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
TAILLE_MAX = 15 * 1024 * 1024


def _inserer(uid, bucket, nom, octets):
    # `le` sert seulement a vider_orphelins ; la derniere version se trouve par
    # _id (deux ecritures dans la meme seconde sont a egalite sur une date).
    return db().fichiers.insert_one({"uid": uid, "bucket": bucket, "nom": nom,
                                     "octets": octets, "le": maintenant()}).inserted_id


def _archiver(f):
    stem, ext = os.path.splitext(f["nom"])
    db().fichiers.update_one({"_id": f["_id"]}, {"$set": {
        "bucket": "_versions", "nom": f"{stem}-{int(time.time())}{ext}"}})


def _poser(item, bucket, role, fichier, extensions=EXTENSIONS, index=None):
    """Corps de deposer() sur un item deja en memoire (creer_soumission le
    pose avant d'inserer le dossier)."""
    ext = os.path.splitext(fichier.filename or "")[1].lower()
    if ext not in extensions:
        raise ValueError(f"format refuse ({ext or 'sans extension'}) : "
                         + ", ".join(sorted(extensions)))
    octets = fichier.read(TAILLE_MAX + 1)
    if len(octets) > TAILLE_MAX:
        raise ValueError(f"fichier trop lourd (max {TAILLE_MAX // 1024 // 1024} Mo)")
    if item["zone"] == "soumissions":
        nom = SAIN.sub("-", role) + (f"_{index}" if index and index > 1 else "") + ext
        perimes = []
    else:
        nom = nom_piece(item, SAIN.sub("-", role), ext, index)
        # Dossier valide d'avant le renommage : le fichier de meme role est encore
        # au nom court et cohabiterait avec le nom lisible. Archive comme un re-depot.
        perimes = [v for v in fichiers_role(item, bucket, role) if v != nom]
    # Le nouveau d'abord, l'ancien ensuite : jamais de fenetre sans piece.
    nouveau = _inserer(item["id"], bucket, nom, octets)
    for f in list(db().fichiers.find({"uid": item["id"], "bucket": bucket,
                                      "nom": {"$in": [nom, *perimes]},
                                      "_id": {"$lt": nouveau}}, {"nom": 1})):
        _archiver(f)
    return nom


def deposer(uid, bucket, role, fichier, extensions=EXTENSIONS, index=None):
    """Ecrit une piece sous un nom semantique. Un re-depot archive l'ancien.

    `extensions` surcharge la liste blanche par defaut : le contrat venu du
    logiciel RH est un PDF ou un .docx, mais on n'elargit PAS EXTENSIONS, qui
    protege le formulaire public.

    `index` (optionnel) : rang 1..N pour les champs multi-fichiers. Le 1er
    fichier garde le nom nu, les suivants prennent un suffixe `_2`, `_3`...

    Nom du fichier : technique (`{role}.{ext}`) tant que le dossier est en
    soumission ; lisible (`<Libellé> - NOM Prénom.ext`, cf. nom_piece) une fois
    validé.
    """
    item = lire(uid)
    if not item:
        raise ValueError(f"dossier inconnu : {uid}")
    return _poser(item, bucket, role, fichier, extensions, index)


def poser_octets(uid, bucket, nom, octets):
    """Ecrit des octets qu'on produit nous-memes (PDF revenu de l'e-signature).
    Ecrase un fichier de meme nom, sans validation d'upload : la source n'est
    pas le formulaire public. -> le nom de fichier ecrit."""
    if not db().dossiers.find_one({"_id": uid}, {"_id": 1}):
        raise ValueError(f"dossier inconnu : {uid}")
    nouveau = _inserer(uid, bucket, nom, octets)
    db().fichiers.delete_many({"uid": uid, "bucket": bucket, "nom": nom,
                               "_id": {"$lt": nouveau}})
    return nom


def ouvrir(uid, bucket, nom):
    """Le contenu d'une piece (octets), ou None si absente."""
    f = db().fichiers.find_one({"uid": uid, "bucket": bucket, "nom": nom},
                               sort=[("_id", -1)])
    return bytes(f["octets"]) if f else None


def fichiers(item, bucket):
    noms = item.get("_fichiers")
    if noms is None:
        noms = _noms({"uid": item["id"]}).get(item["id"], {})
    return noms.get(bucket, [])


def _effacer_fichiers(uid):
    """Tous les fichiers de l'uid, _versions compris. Idempotent."""
    db().fichiers.delete_many({"uid": uid})


def vider_orphelins():
    """Fichiers de plus d'une heure sans dossier : un depot ou un import
    interrompu (arret du serveur) entre les fichiers et insert_one. -> nombre
    d'uids nettoyes."""
    limite = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    vieux = {f["uid"] for f in db().fichiers.find({"le": {"$lt": limite}}, {"uid": 1})}
    orph = vieux - set(db().dossiers.distinct("_id", {"_id": {"$in": list(vieux)}}))
    if orph:
        db().fichiers.delete_many({"uid": {"$in": sorted(orph)}})
    return len(orph)


# Libelle lisible d'un role de piece. CONTRAINTE : chaque valeur doit se
# re-sluguer vers sa cle -- SAIN.sub("-", sans_accent(v)).strip("-").lower() == k
# --, sinon _extraire_role() ne retrouve plus le role depuis un nom lisible.
# D'ou "Identite" et pas "Piece d'identite".
LIBELLES_PIECE = {"contrat": "Contrat", "contrat-signe": "Contrat signé",
                  "accuse-dpae": "Accusé DPAE", "fiche-salarie": "Fiche salarié",
                  "identite": "Identité", "carte-vitale": "Carte vitale",
                  "rib": "RIB"}


def date_sortie(item):
    """Date de sortie saisie a l'archivage ('' si le dossier n'est pas Parti).
    Meme forme que date_remise : la date vit dans le journal, pas un champ a
    part."""
    return next((e.get("date_sortie", "") for e in reversed(item["journal"])
                if e.get("vers") == "Parti"), "")


def est_remise(e):
    """Entree de journal qui remet (ou renvoie) le dossier au comptable.
    noter() ecrit aussi vers == RemisComptable sur un dossier deja remis
    (acces_lot, contrat_signe...) : ces notes ne sont pas des remises, sinon
    l'ouverture du lot par le cabinet le renvoyait au recap suivant."""
    return ((e.get("vers") == "RemisComptable" and e.get("de") != "RemisComptable")
            or e.get("type") == "renvoi_comptable")


def _datee(e):
    """Une remise fige son jour LOCAL (`jour`) : `le` est en UTC, et entre 0 h et
    2 h à Paris il nommerait les fichiers du lot de la veille. Les entrées plus
    anciennes, sans `jour`, gardent leur date UTC : leurs noms sont déjà partis."""
    if est_remise(e):
        e["jour"] = date.today().isoformat()
    return e


def date_remise(item, repli=True):
    """Jour (AAAA-MM-JJ) de la derniere remise/renvoi au comptable ; repli
    aujourd'hui (repli=False : "" -- un affichage ne doit pas dater du jour un
    dossier jamais remis, ex. importe). Fige la date des noms de fichiers : un
    cabinet qui telecharge le lot une semaine plus tard garde la date de la
    remise, pas celle du clic."""
    jours = [e.get("jour") or e["le"][:10] for e in item["journal"] if est_remise(e)]
    if jours:
        return jours[-1]
    return date.today().isoformat() if repli else ""


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


def fichiers_compta(item, bucket):
    """Fichiers d'un bucket partages avec le cabinet (lot comptable) : tout
    sauf le contrat signe, garde cote RH pour l'instant."""
    return [f for f in fichiers(item, bucket)
            if not (bucket == "contrat" and _extraire_role(f) == "contrat-signe")]


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


# Etat ou le contrat signe peut se deposer (ou partir en signature) : une fois
# le salarie remis au cabinet, depuis sa fiche dans la vue Salaries.
SIGNE_DEPOSABLE = {"RemisComptable"}


def signe_manquant(item):
    """Contrat signe attendu mais pas encore depose (badge salaries,
    garde du depot et de l'envoi en signature)."""
    # Importe : le dossier etait en ordre dans sa source, ses fichiers gardent
    # leur nom d'origine (aucun role lisible) -- rien n'est attendu.
    return (etat(item) in SIGNE_DEPOSABLE and not item.get("purge")
            and not item.get("import_cle")
            and not fichiers_role(item, "contrat", "contrat-signe"))


def manquantes(item):
    """Pieces requises par le schema et absentes du dossier.

    Retourne une liste de dicts {'champ': config}.  `max_fichiers` est le
    nombre exigé : une piece est satisfaite quand son role compte au moins
    ce nombre de fichiers (1 par defaut).
    Compatible bool : [] == tout est la.
    """
    # Un dossier purge n'a plus ses pieces (effacees expres) : sans ce garde,
    # toutes les pieces requises repasseraient en « manquantes » et /suivi
    # afficherait un rouge permanent et faux. Couvre les quatre appelants
    # (suivi, detail, _saisie, _fiche_salarie).
    if item.get("purge") or item.get("import_cle"):     # importe : cf. signe_manquant
        return []
    comptes = Counter(_extraire_role(f) for f in fichiers(item, "pieces"))
    return [{"champ": c} for c in config.pieces()
            if c.get("requis")
            and comptes[SAIN.sub("-", c["role"])] < c.get("max_fichiers", 1)]


# --- cycle de vie --------------------------------------------------------

def _journaliser(uid, construire):
    """Le seul ecrivain du journal. `construire(item frais)` -> (entree, $set,
    $inc), ou None pour ne rien ecrire. La mise a jour n'est appliquee que si
    l'etat n'a pas bouge depuis la lecture (filtre sur le cache `etat`) ; sinon
    on relit et reconstruit : un double clic rejoue la garde de TRANSITIONS sur
    l'etat frais au lieu d'ecrire deux fois. -> item apres ecriture."""
    for _ in range(3):
        item = lire(uid)
        if item is None:
            raise ValueError(f"dossier inconnu : {uid}")
        maj = construire(item)
        if maj is None:
            return item
        entree, pose, inc = maj
        op = {"$push": {"journal": entree}, "$set": {**pose, "etat": entree["vers"]}}
        if inc:
            op["$inc"] = inc
        doc = db().dossiers.find_one_and_update({"_id": uid, "etat": etat(item)}, op,
                                                return_document=ReturnDocument.AFTER)
        if doc:
            return _vue(doc, item["_fichiers"])
    raise RuntimeError(f"{uid} : modifié en continu, écriture abandonnée après 3 essais")


def _deposer_champ(item, c, fichiers_recus):
    """Depose le(s) fichier(s) recu(s) pour un champ piece_jointe.

    `fichiers_recus` peut etre un MultiDict (request.files) ou un simple dict
    (import du logiciel RH, tests). Le multi-fichiers n'est possible que sur le premier.
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
            _poser(item, "pieces", c["role"], f, index=rang)


def _segment(brut, defaut):
    """Un composant de chemin sur : illegaux Windows remplaces, points/espaces
    de fin otes (NTFS les refuse), 60 car. max (MAX_PATH)."""
    s = _ILLEGAL.sub("-", str(brut or "")).strip().rstrip(". ")[:60].strip()
    return s or defaut


def _hierarchie(item):
    """(groupe, etablissement, poste, "NOM Prenom") brut, non segmente : les
    niveaux de l'arborescence d'export."""
    ch = item["champs"]
    cle = config.valeur(ch, "etablissement")
    try:
        groupe, etab = config.groupe(cle), config.etablissement(cle)[1]["nom"]
    except KeyError:                       # etablissement retire de la config
        groupe, etab = "DIVERS", cle
    prenom, nom = config.identite(ch)
    qui = " ".join(p for p in (nom.upper(), prenom) if p) or item["id"]
    return groupe, etab, config.valeur(ch, "poste"), qui


def creer_soumission(champs, fichiers_recus):
    uid = nouvel_id()
    # Version du FORMULAIRE client au moment de la soumission (pas un schema de
    # stockage : rien ne le relit, c'est une trace pour comprendre un vieux
    # dossier si le formulaire a change depuis).
    doc = {"_id": uid, "zone": "soumissions", "etat": "Soumise",
           "formulaire_version": config.formulaire()["version"],
           "champs": champs, "link_epoch": 0,
           "journal": [{"de": None, "vers": "Soumise", "le": maintenant(),
                        "par": "formulaire"}]}
    try:
        for c in config.pieces():
            _deposer_champ(_vue(doc, None), c, fichiers_recus)
        db().dossiers.insert_one(doc)
    except BaseException:           # piece refusee : pas de dossier a moitie ne
        _effacer_fichiers(uid)
        raise
    return uid


# --- import de salaries deja en poste (bouton « Récupérer les dossiers ») ---
# Un salarie embauche avant l'outil a deja un dossier en ordre ailleurs
# (SharePoint) : rien a verifier, rien a classer. Son dossier nait directement
# a RemisComptable (visible dans /salaries), fichiers sous leur nom d'origine,
# et n'affiche jamais de piece manquante (cf. manquantes, signe_manquant).

def importer(champs, cle, entree, fichiers, racine=None):
    """Cree le dossier d'un salarie deja en poste, a RemisComptable.

    `cle` l'identifie dans sa source : une cle deja importee ne fait rien
    (rejouable, jamais d'ecrasement ; l'index unique `import_cle` tranche une
    course). `entree` complete l'entree de journal (par, origine...) ; `racine`
    va a la racine du dossier, que la purge garde. `fichiers` = [(bucket, nom,
    ouvrir)], ouvrir() -> octets. Fichiers d'abord, dossier ensuite : un echec
    efface les fichiers deja ecrits. -> uid, ou None si deja importe."""
    if db().dossiers.find_one({"import_cle": cle}, {"_id": 1}):
        return None
    uid = nouvel_id()
    # de == vers, comme noter() : etat() lit "vers", et ni le recap hebdo
    # (est_remise) ni la purge (_entree_etat_courant) n'y voient une remise.
    doc = {"_id": uid, "zone": "documents", "etat": "RemisComptable",
           "formulaire_version": config.formulaire()["version"],
           "champs": champs, "link_epoch": 0, "lien_comptable_epoch": 0,
           "import_cle": cle, **(racine or {}),
           "journal": [{"de": "RemisComptable", "vers": "RemisComptable",
                        "le": maintenant(), "type": "import", **entree}]}
    pris = set()
    try:
        for bucket, nom, lire_octets in fichiers:
            # nom d'origine : sans separateur (il finit dans une URL et un zip),
            # numerote plutot qu'ecraser un homonyme venu d'un autre sous-dossier
            nom = _ILLEGAL.sub("-", nom).strip().rstrip(". ") or "fichier"
            stem, ext = os.path.splitext(nom)
            n = 2
            while (bucket, nom) in pris:
                nom, n = f"{stem} ({n}){ext}", n + 1
            pris.add((bucket, nom))
            _inserer(uid, bucket, nom, lire_octets())
        db().dossiers.insert_one(doc)
    except DuplicateKeyError:
        _effacer_fichiers(uid)
        return None
    except BaseException:
        _effacer_fichiers(uid)
        raise
    return uid


def resoumettre(uid, champs, fichiers_recus):
    """Correction apres KO : la MEME soumission repasse a Soumise."""
    item = lire(uid)
    for c in config.pieces():
        _deposer_champ(item, c, fichiers_recus)
    _journaliser(uid, lambda it: ({"de": etat(it), "vers": "Soumise",
                                   "le": maintenant(), "par": "formulaire",
                                   "motif": "correction"}, {"champs": champs}, None))


def valider(uid, par):
    """OK de la RH : la soumission devient un dossier salarie (meme ULID)."""
    item = lire(uid)
    if not item or item["zone"] != "soumissions":
        raise ValueError("deja un dossier salarie")
    # Le dossier valide porte des noms lisibles : les pieces du candidat (noms
    # techniques) sont renommees, comme tout ce qui sera depose/produit ensuite.
    # Idempotent (un nom deja lisible se re-rend identique).
    pieces = fichiers(item, "pieces")
    for nom in pieces:
        voulu = nom_piece(item, _extraire_role(nom), os.path.splitext(nom)[1], _index_nom(nom))
        if voulu != nom and voulu not in pieces:
            db().fichiers.update_many({"uid": uid, "bucket": "pieces", "nom": nom},
                                      {"$set": {"nom": voulu}})

    def construire(it):
        if it["zone"] != "soumissions":
            raise ValueError("deja un dossier salarie")
        return ({"de": etat(it), "vers": "ATraiter", "le": maintenant(), "par": par},
                {"zone": "documents", "lien_comptable_epoch": 0}, None)
    _journaliser(uid, construire)


def archiver(uid, par, date_sortie, motif=""):
    """Depart du salarie : etat Parti (onglet « Anciens salariés », racine
    LEAVERS/ de l'export)."""
    item = lire(uid)
    if not item or etat(item) != "RemisComptable":
        raise ValueError("seul un dossier remis au comptable peut être archivé")
    return transition(uid, "Parti", par, date_sortie=date_sortie, motif=motif)


def rejeter(uid, motif, commentaire, par):
    _journaliser(uid, lambda it: ({"de": etat(it), "vers": "Rejetee",
                                   "le": maintenant(), "par": par, "motif": motif,
                                   "commentaire": commentaire},
                                  {}, {"link_epoch": 1}))          # tue l'ancien lien


def transition(uid, vers, par, **extra):
    def construire(item):
        de = etat(item)
        if vers not in TRANSITIONS.get(de, set()):
            raise ValueError(f"transition interdite : {de} -> {vers}")
        return _datee({"de": de, "vers": vers, "le": maintenant(), "par": par, **extra}), {}, None
    return _journaliser(uid, construire)


def completer_champs(uid, nouveaux):
    """Fusionne des valeurs saisies apres la soumission (saisie RH a la
    generation du contrat) dans item['champs']. Idempotent, ne journalise pas ;
    dernier ecrivain gagne, champ par champ."""
    item = lire(uid)
    if item is None:
        raise ValueError("dossier inconnu")
    pose = {f"champs.{k}": v for k, v in nouveaux.items() if v != ""}
    if not pose:
        return item
    doc = db().dossiers.find_one_and_update({"_id": uid}, {"$set": pose},
                                            return_document=ReturnDocument.AFTER)
    if doc is None:
        raise ValueError("dossier inconnu")
    return _vue(doc, item["_fichiers"])


def completer(uid, nouveaux, par, type, vides_seulement=False, effacer=False):
    """Complete un dossier importe (tableau ou Excel de « Compléter les
    dossiers »). Une valeur vide n'efface que si `effacer` (saisie de la RH,
    l'appelant garde le verrou optimiste) ; `vides_seulement` ne remplit que
    les champs encore vides. Le journal porte les ids des champs, jamais leurs
    valeurs. -> [ids ecrits]."""
    faits = {}

    def construire(item):
        # noter() recopie l'etat : sur un Parti, l'entree masquerait la date de sortie
        if not item.get("import_cle") or etat(item) != "RemisComptable":
            raise ValueError("seul un dossier importé, en poste, se complète ici")
        ch = item["champs"]
        faits.clear()
        faits.update({k: v for k, v in nouveaux.items()
                      if (v != "" or effacer) and v != ch.get(k, "")
                      and not (vides_seulement and ch.get(k))})
        if not faits:
            return None
        return ({"de": etat(item), "vers": etat(item), "le": maintenant(),
                 "par": par, "type": type, "champs": sorted(faits)},
                # champ par champ : le filtre de _journaliser ne porte que sur
                # l'etat, un $set du dict entier ecraserait un ecrivain concurrent
                {f"champs.{k}": v for k, v in faits.items()}, None)
    _journaliser(uid, construire)
    return sorted(faits)


def noter(uid, **entree):
    """Entree de journal sans changement d'etat (acces au lot, mail parti...)."""
    _journaliser(uid, lambda it: (_datee({"de": etat(it), "vers": etat(it),
                                          "le": maintenant(), **entree}), {}, None))


def bump_epoch(uid, cle):
    doc = db().dossiers.find_one_and_update({"_id": uid}, {"$inc": {cle: 1}},
                                            return_document=ReturnDocument.AFTER)
    if doc is None:
        raise ValueError("dossier inconnu")
    return doc[cle]


# --- conservation / purge ----------------------------------------------
#
# La purge EFFACE les pieces et garde une trace dans le journal. Elle vit dans
# une requete de l'app (bouton RH) ; purger.py ne fait que LIRE et lister.

_CHAMPS_GARDES = ("etablissement", "poste", "date_debut")   # roles que /suivi lit
_JOURNAL_GARDE = ("le", "jour", "de", "vers", "par", "type")


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
    # importe : RemisComptable des l'import, mais toujours en poste
    if item.get("import_cle") and etat(item) != "Parti":
        return None
    duree = (conf.get("jours_candidature", conf["jours"])
             if etat(item) == "Soumise" else conf["jours"])
    ecoule = (datetime.now(timezone.utc)
              - datetime.fromisoformat(_entree_etat_courant(item))).days
    return (ecoule, duree) if ecoule >= duree else None


def eligibles(items=None):
    """[(item, jours_ecoules, duree)] des dossiers purgables. `items` :
    collection deja lue (/suivi la passe pour ne pas relire)."""
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


def purger(uid):
    """Efface les pieces d'un dossier eligible, garde le journal. Rejouable :
    un second passage ne leve pas et ne change rien.

    Le marqueur `purge` est pose AVANT l'effacement, sous condition que le
    dossier n'ait pas bouge depuis la lecture (journal de meme longueur) : un
    renvoi ou un abandon entre-temps a pu le rendre ineligible. Marqueur deja
    la = reprise directe a l'effacement."""
    item = lire(uid)
    if not item:
        return
    if not item.get("purge"):
        elig = _eligibilite(item, config.conservation())
        if not elig:
            return
        courant = etat(item)
        champs = {config.role(r): config.valeur(item["champs"], r)
                  for r in _CHAMPS_GARDES if config.valeur(item["champs"], r)}
        journal = _journal_nettoye(item["journal"]) + [
            {"de": courant, "vers": courant, "le": maintenant(),
             "par": "systeme", "type": "purge"}]
        db().dossiers.update_one(
            {"_id": uid, "purge": {"$exists": False},
             "journal": {"$size": len(item["journal"])}},
            # epochs : sinon un jeton de moins de 30 j reste valide sur un dossier vide
            {"$set": {"champs": champs, "journal": journal, "etat": courant,
                      "purge": {"le": maintenant(), "jours": elig[1]}},
             "$inc": {"link_epoch": 1, "lien_comptable_epoch": 1}})
        if not db().dossiers.find_one({"_id": uid, "purge": {"$exists": True}}, {"_id": 1}):
            return          # le dossier a bouge : rien n'est efface, le prochain clic revérifie
    _effacer_fichiers(uid)


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
