"""Bouton « Récupérer les dossiers » : importe les salariés déjà en poste depuis
les dossiers d'un SharePoint (Microsoft Graph), un établissement à la fois.

Connexion : compte Microsoft de la RH, code à saisir sur le site de Microsoft
(device flow MSAL, client public Office : ni app Azure ni consentement admin).
Jeton en cache dans DONNEES/sharepoint-jeton.json, effacé par deconnecter().

Actif seulement si instance.json porte un bloc `sharepoint` ; tout le vocabulaire
du client y vit (aucun nom propre à un client ici) :
    racine        dossier lu : <racine>/<groupe>/<établissement>/<poste>/<salarié>/
    ignorer       sous-dossiers sautés à tout niveau (salariés partis, copies...)
    mots_ignores  mots trop communs pour rapprocher établissement et dossier
    postes        dossier de poste -> poste du formulaire
    dossiers      « Société / Établissement » -> dossier, quand le nom ne suffit pas
Pris : les fichiers à la racine du dossier salarié, CONTRAT et FICHE PERSONNELLE.
Rien d'autre (dossiers médicaux, disciplinaires...).
"""
import json
import re
import shutil
import threading
import time
import unicodedata
from urllib.parse import quote, urlparse

import config
import store

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/.default"]
CLIENT_OFFICE = "d3590ed6-52b3-4102-aeff-aad2292ab01c"   # client public Office
TAILLE_MAX = 50 * 1024 * 1024    # au-delà : fichier laissé à la RH (vidéo, scan géant)
# Disque plein = plus aucune écriture (soumissions, journaux) : l'app tombe pour tous.
# simplification volontaire : marge fixe, à rendre configurable si un client a un disque très petit
MARGE_DISQUE = 1024 ** 3

_TRANSPORT = None   # tests : fn(url, entetes) -> (statut, octets, entetes)
_session = None


class ErreurSharePoint(RuntimeError):
    """Réponse Graph en erreur, message lisible par la RH."""


class NonConnecte(RuntimeError):
    """Aucun compte Microsoft en cache : passer par connecter()."""


class EspaceInsuffisant(ErreurSharePoint):
    """L'import s'arrête avant de remplir le disque du serveur."""


# --- partie pure : noms, postes, fichiers retenus ---------------------------

def _norm(s):
    """Minuscules, sans accents (NFC comme NFD d'iPhone), espaces réduits."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _ignores():
    return {_norm(n) for n in config.sharepoint().get("ignorer") or []}


def _singulier(s):
    return re.sub(r"s\b", "", s)


def analyser_nom(nom_dossier):
    """« DUPONT Marie (18H) » -> ("DUPONT Marie", "18H", "18H").
    -> (nom, heures « NNH » ou "", suffixe brut entre parenthèses ou "")."""
    m = re.search(r"\s*\(([^)]*)\)\s*$", nom_dossier)
    nom = (nom_dossier[:m.start()] if m else nom_dossier).strip()
    suffixe = m.group(1).strip() if m else ""
    h = re.search(r"(\d{1,2})\s*h\b", suffixe, re.I)
    return nom, (f"{int(h.group(1))}H" if h else ""), suffixe


def poste_de(nom_dossier):
    """Poste du formulaire pour un dossier de poste SharePoint. D'abord les
    alias de instance.json (sharepoint.postes), puis une option du formulaire
    au même nom au pluriel près. Inconnu -> "" (la RH complète)."""
    n = _singulier(_norm(nom_dossier))
    for dossier, poste in (config.sharepoint().get("postes") or {}).items():
        if _singulier(_norm(dossier)) == n:
            return poste
    for option in config.champ(config.role("poste")).get("options") or []:
        if _singulier(_norm(option)) == n:
            return option
    return ""


def champs_de(cle_etab, salarie):
    """Champs d'un dossier importé : établissement, nom, poste, heures si
    elles figurent dans les options du formulaire. Le reste reste vide."""
    ch = {config.role("etablissement"): cle_etab, config.role("nom"): salarie["nom"]}
    if salarie["poste"]:
        ch[config.role("poste")] = salarie["poste"]
    tt = config.role("temps_travail")
    if salarie["heures"] and salarie["heures"] in (config.champ(tt).get("options") or []):
        ch[tt] = salarie["heures"]
    return ch


def salaries(enfants, etab_id):
    """Dossiers salariés d'un établissement SharePoint (deux niveaux :
    poste puis salarié). `enfants(item_id)` -> [driveItem]."""
    out, ignores = [], _ignores()
    for poste in enfants(etab_id):
        if "folder" not in poste or _norm(poste["name"]) in ignores:
            continue
        for sal in enfants(poste["id"]):
            if "folder" not in sal or _norm(sal["name"]) in ignores:
                continue
            nom, heures, suffixe = analyser_nom(sal["name"])
            out.append({"id": sal["id"], "dossier": f"{poste['name']}/{sal['name']}",
                        "nom": nom, "heures": heures, "suffixe": suffixe,
                        "poste_brut": poste["name"], "poste": poste_de(poste["name"])})
    return out


def _bucket_du_sous_dossier(nom):
    n = _norm(nom).replace(" ", "")
    if n == "contrat":
        return "contrat"
    if n.startswith("fichepers"):           # « FICHE PERSONELLE » existe aussi
        return "pieces"
    return None


def _tous_fichiers(enfants, item_id):
    out = []
    for x in enfants(item_id):
        if "file" in x:
            out.append(x)
        elif "folder" in x:
            out += _tous_fichiers(enfants, x["id"])
    return out


def fichiers_a_prendre(enfants, salarie_id):
    """[(bucket, driveItem)] : racine -> pieces, CONTRAT -> contrat,
    FICHE PERSONNELLE -> pieces (sous-dossiers compris). Rien d'autre."""
    out = []
    for x in enfants(salarie_id):
        if "file" in x:
            out.append(("pieces", x))
        elif "folder" in x and (b := _bucket_du_sous_dossier(x["name"])):
            out += [(b, f) for f in _tous_fichiers(enfants, x["id"])]
    return out


def cle(salarie_id):
    return "sharepoint:" + salarie_id


def deja_importes():
    return {i["import_cle"] for i in store.tout() if i.get("import_cle")}


def cle_nom(*morceaux):
    """Mots d'un nom, sans ordre, accents ni casse : « Dupont Jean » = « JEAN DUPONT »."""
    return frozenset(re.findall(r"[a-z0-9]+", _norm(" ".join(morceaux))))


def deja_dans_app(cle_etab):
    """{cle_nom: dossier} des salariés de cet établissement venus du parcours
    normal (les importés relèvent de deja_importes ; une embauche abandonnée ne
    compte pas). Le SharePoint peut porter le nom d'usage comme celui de
    naissance : une clé pour chacun."""
    out = {}
    for i in store.tout():
        ch = i["champs"]
        if (i.get("import_cle") or store.etat(i) == "Abandonnee"
                or config.valeur(ch, "etablissement") != cle_etab):
            continue
        for r in ("nom_usage", "nom_naissance", "nom"):
            if n := config.valeur(ch, r):
                out.setdefault(cle_nom(n, config.valeur(ch, "prenom")), i)
    return out


def liens():
    """{clé d'établissement de l'app: dossier SharePoint} noués par les imports
    déjà faits. Le premier import d'un établissement fixe son dossier."""
    out = {}
    for i in reversed(store.tout()):                       # plus ancien d'abord
        # racine du dossier (la purge la garde) ; journal = imports d'avant
        if i.get("import_cle") and (sp := i.get("sp_etab") or i["journal"][0].get("sp_etab")):
            out.setdefault(config.valeur(i["champs"], "etablissement"), sp)
    return out


def _mots(s):
    vides = {_norm(m) for m in config.sharepoint().get("mots_ignores") or []}
    return {m for m in re.findall(r"[a-z0-9]+", _norm(s)) if len(m) > 2 and m not in vides}


def _ressemblance(cle_etab, dossier):
    """Mots communs entre le nom de l'établissement et celui du dossier
    (« ACME / Paris Opéra » ~ « Opéra »), +1 si le groupe (RESTAURANTS,
    DARK KITCHENS) est le même. Les mots d'une société à plusieurs
    établissements ne comptent pas : partagés, ils lieraient le dernier dossier
    libre à tort. Ceux d'une société à un seul établissement le désignent
    (« ACME Nantes / Centre » ~ « ACME Nantes »)."""
    groupe, nom = dossier["chemin"].split("/", 1)
    s, e = config.etablissement(cle_etab)
    if len(s["etablissements"]) > 1:
        mots = _mots(e["nom"]) - _mots(s["nom"])
    else:
        mots = _mots(e["nom"]) | _mots(s["nom"])
    score = len(mots & _mots(nom))
    return score + (score > 0 and _norm(config.groupe(cle_etab)) == _norm(groupe))


def dossier_de(cle_etab, dossiers):
    """Le dossier SharePoint de cet établissement, ou None. La RH ne choisit
    jamais : dossier déjà lié par un import, sinon celui que l'installateur a
    écrit (instance.json, sharepoint.dossiers), sinon le seul nettement le plus
    ressemblant. Un dossier lié à un autre établissement n'est jamais rendu."""
    lien = liens()
    if cle_etab in lien:
        return next((d for d in dossiers if d["id"] == lien[cle_etab]["id"]), lien[cle_etab])
    pris = {sp["id"] for sp in lien.values()}
    libres = [d for d in dossiers if d["id"] not in pris]
    if force := (config.sharepoint().get("dossiers") or {}).get(cle_etab):
        return next((d for d in libres if _norm(d["chemin"]) == _norm(force)), None)
    notes = sorted(((_ressemblance(cle_etab, d), d) for d in libres), key=lambda n: -n[0])
    if notes and notes[0][0] > 0 and (len(notes) == 1 or notes[0][0] > notes[1][0]):
        return notes[0][1]
    return None                                            # aucun, ou ambigu


# --- HTTP Graph ----------------------------------------------------------------

def _http(url, entetes):
    global _session
    if _TRANSPORT:
        return _TRANSPORT(url, entetes)
    if _session is None:
        import requests                 # dépendance de msal, garde le keep-alive
        _session = requests.Session()
    r = _session.get(url, headers=entetes, timeout=120)
    return r.status_code, r.content, r.headers


def _get(url, jeton=None, brut=False):
    """GET avec jeton (sauf adresse pré-signée), 3 reprises sur 429/503."""
    entetes = {"Authorization": f"Bearer {jeton}"} if jeton else {}
    for essai in range(4):
        statut, octets, h = _http(url, entetes)
        if statut in (429, 503) and essai < 3:
            time.sleep(min(float(h.get("Retry-After") or 2 ** essai), 60))
            continue
        if statut >= 400:
            raise ErreurSharePoint(f"SharePoint a répondu {statut} : {octets[:200]!r}")
        return octets if brut else json.loads(octets)


def _lister(url, jeton):
    items = []
    while url:
        page = _get(url, jeton)
        items += page.get("value", [])
        url = page.get("@odata.nextLink")
    return items


def _drive(jeton):
    u = urlparse(config.sharepoint()["site"])
    site = _get(f"{GRAPH}/sites/{u.netloc}:{quote(u.path.rstrip('/'))}", jeton)
    return f"{GRAPH}/sites/{site['id']}/drive"


def _enfants(drive):
    return lambda item_id: _lister(f"{drive}/items/{item_id}/children?$top=200", jeton())


def ouvrir(item):
    """Octets d'un fichier, par son adresse pré-signée : sans jeton."""
    return _get(item["@microsoft.graph.downloadUrl"], brut=True)


def etablissements_sp():
    """[{id, chemin}] des dossiers d'établissement : <racine>/<groupe>/<établissement>."""
    jt = jeton()
    drive = _drive(jt)
    racine = config.sharepoint()["racine"].strip("/")
    out, ignores = [], _ignores()
    for g in _lister(f"{drive}/root:/{quote(racine)}:/children", jt):
        if "folder" not in g or _norm(g["name"]) in ignores:
            continue
        for e in _lister(f"{drive}/items/{g['id']}/children?$top=200", jt):
            if "folder" in e and _norm(e["name"]) not in ignores:
                out.append({"id": e["id"], "chemin": f"{g['name']}/{e['name']}"})
    return out


def lister_salaries(sp_etab_id):
    return salaries(_enfants(_drive(jeton())), sp_etab_id)


# --- connexion Microsoft (device flow) ------------------------------------------

_msal = None                      # (app, cache) : un seul par process
_conn = {"etat": "vierge"}        # vierge|demarrage|attente|connecte|erreur
_conn_lock = threading.Lock()
_conn_code_pret = threading.Event()


def _cache_fichier():
    return config.DONNEES / "sharepoint-jeton.json"


def _app():
    global _msal
    if _msal is None:
        try:                            # import paresseux : instance sans bouton = sans msal
            import msal
        except ImportError:
            raise ErreurSharePoint("Le module de connexion Microsoft n'est pas installé : "
                                   "pip install -r requirements-sharepoint.txt") from None
        conf = config.sharepoint()
        cache = msal.SerializableTokenCache()
        try:
            if _cache_fichier().exists():
                cache.deserialize(_cache_fichier().read_text(encoding="utf-8"))
        except ValueError:                  # jeton corrompu : reconnexion
            _cache_fichier().unlink(missing_ok=True)
        # organizations : le tenant du compte, quel que soit le domaine du site
        _msal = (msal.PublicClientApplication(
            conf.get("client_id") or CLIENT_OFFICE,
            authority="https://login.microsoftonline.com/organizations", token_cache=cache), cache)
    return _msal


def _sauver(cache):
    if cache.has_state_changed:
        _cache_fichier().write_text(cache.serialize(), encoding="utf-8")
        cache.has_state_changed = False


def jeton():
    if not _cache_fichier().exists():
        raise NonConnecte("Connectez-vous à SharePoint.")
    app, cache = _app()
    comptes = app.get_accounts()
    res = app.acquire_token_silent(SCOPES, account=comptes[0]) if comptes else None
    if not res or "access_token" not in res:
        raise NonConnecte("Session Microsoft expirée : reconnectez-vous.")
    _sauver(cache)
    return res["access_token"]


def compte():
    """Adresse du compte connecté, ou None. Sans réseau."""
    if not _cache_fichier().exists():
        return None
    comptes = _app()[0].get_accounts()
    return comptes[0]["username"] if comptes else None


def etat_connexion():
    with _conn_lock:
        e = dict(_conn)
    if e["etat"] in ("vierge", "connecte"):
        try:
            qui = compte()
        except ErreurSharePoint as err:                     # msal retiré après une connexion
            return {"etat": "erreur", "erreur": str(err)}
        e = {"etat": "connecte", "compte": qui} if qui else {"etat": "vierge"}
    return e


def connecter():
    """Lance le device flow (ou rend celui en cours). Attend le code ~20 s."""
    with _conn_lock:
        en_cours = _conn["etat"] in ("demarrage", "attente")
        if not en_cours:
            _conn.clear()
            _conn["etat"] = "demarrage"
            _conn_code_pret.clear()
    if not en_cours:
        threading.Thread(target=_flux, daemon=True).start()
    _conn_code_pret.wait(timeout=20)
    return etat_connexion()


def _flux():
    def fin(**etat):
        with _conn_lock:
            _conn.clear()
            _conn.update(etat)
        _conn_code_pret.set()
    try:
        app, cache = _app()
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            return fin(etat="erreur", erreur=flow.get("error_description", "code refusé"))
        with _conn_lock:
            _conn.update(etat="attente", code=flow["user_code"], url=flow["verification_uri"])
        _conn_code_pret.set()
        res = app.acquire_token_by_device_flow(flow)       # bloque jusqu'à validation
        if "access_token" not in res:
            return fin(etat="erreur", erreur=res.get("error_description", "connexion refusée"))
        _sauver(cache)
        fin(etat="connecte")
    except Exception as e:                                  # noqa: BLE001 — affiché à la RH
        fin(etat="erreur", erreur=f"{type(e).__name__} : {e}")


def deconnecter():
    global _msal
    _cache_fichier().unlink(missing_ok=True)
    _msal = None
    with _conn_lock:
        _conn.clear()
        _conn["etat"] = "vierge"


# --- import en tâche de fond ------------------------------------------------------
# Un seul import à la fois pour toute l'app : deux établissements de l'app
# pointant le même dossier SharePoint, importés en parallèle, passeraient tous
# deux le test d'idempotence de store.importer.

_import = {"etat": "aucun"}       # aucun|en_cours|fini
_import_lock = threading.Lock()


def avancement():
    with _import_lock:
        return {**_import, "erreurs": list(_import.get("erreurs", []))}


def lancer_import(cle_etab, sp_etab, ids, par):
    """-> False si un import tourne déjà. ValueError si le dossier n'est pas
    celui de cet établissement (lié à un autre, ou établissement déjà lié)."""
    with _import_lock:
        if _import["etat"] == "en_cours":
            return False
        lien = liens()
        if (lien.get(cle_etab, sp_etab)["id"] != sp_etab["id"]
                or any(c != cle_etab and sp["id"] == sp_etab["id"] for c, sp in lien.items())):
            raise ValueError(f"{sp_etab['chemin']} n'est pas le dossier SharePoint de {cle_etab}.")
        _import.clear()
        _import.update(etat="en_cours", cle=cle_etab, chemin=sp_etab["chemin"],
                       fait=0, total=len(ids), crees=0, erreurs=[])
    threading.Thread(target=_travail, args=(cle_etab, sp_etab, set(ids), par),
                     daemon=True).start()
    return True


def _travail(cle_etab, sp_etab, ids, par):
    try:
        store.vider_imports()
        enfants = _enfants(_drive(jeton()))
        # recoupé côté serveur : seuls des ids vraiment présents sont importés
        retenus = [s for s in salaries(enfants, sp_etab["id"]) if s["id"] in ids]
        with _import_lock:
            _import["total"] = len(retenus)
        deja = deja_importes()                              # un seul scan par import
        for s in retenus:
            _un_salarie(cle_etab, sp_etab, s, enfants, par, deja)
    except EspaceInsuffisant as e:                          # arrête tout l'import
        with _import_lock:
            _import["erreurs"].append(str(e))
    except Exception as e:                                  # noqa: BLE001 — affiché à la RH
        with _import_lock:
            _import["erreurs"].append(f"{type(e).__name__} : {e}")
    finally:
        with _import_lock:
            _import["etat"] = "fini"


def _un_salarie(cle_etab, sp_etab, s, enfants, par, deja):
    try:
        if cle(s["id"]) not in deja:
            pris = fichiers_a_prendre(enfants, s["id"])
            fichiers = [(b, it["name"], lambda it=it: ouvrir(it))
                        for b, it in pris if it.get("size", 0) <= TAILLE_MAX]
            gros = [it for _, it in pris if it.get("size", 0) > TAILLE_MAX]
            besoin = sum(it.get("size", 0) for _, it in pris if it.get("size", 0) <= TAILLE_MAX)
            if shutil.disk_usage(config.DONNEES).free - besoin < MARGE_DISQUE:
                raise EspaceInsuffisant("Espace disque insuffisant sur le serveur : import arrêté. "
                                        "Libérez de la place ou contactez l'installateur.")
            entree = {"par": par, "origine": "sharepoint", "sp_dossier": s["dossier"]}
            if store.importer(champs_de(cle_etab, s), cle(s["id"]), entree, fichiers,
                              racine={"sp_etab": sp_etab}):
                with _import_lock:
                    _import["crees"] += 1
                    _import["erreurs"] += [
                        f"{s['nom']} : {it['name']} ({round(it['size'] / 2**20)} Mo) "
                        "non importé, à récupérer à la main" for it in gros]
    except EspaceInsuffisant:
        raise
    except Exception as e:                                  # noqa: BLE001 — on passe au suivant
        with _import_lock:
            _import["erreurs"].append(f"{s['nom']} : {type(e).__name__} : {e}")
    with _import_lock:
        _import["fait"] += 1
