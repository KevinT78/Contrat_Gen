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


def comptes():
    """Les comptes de connexion : le bloc `utilisateurs` de instance.json."""
    return instance().get("utilisateurs", {})


def stockage():
    """Le bloc `stockage` d'instance.json : ou vivent les donnees.

    Lecture GARDEE : config est importe par des outils qui tournent sans
    instance complete (doctor, tests, installer d'a cote). Une instance
    installee avant ce bloc n'en a pas -- absent vaut « local »."""
    try:
        s = instance().get("stockage")
    except (OSError, ValueError, KeyError):
        return {}
    return s if isinstance(s, dict) else {}


def conservation():
    """Le bloc `conservation` d'instance.json : au bout de combien de jours dans
    un etat terminal les pieces d'un dossier sont effacees (le journal, lui,
    reste). Lecture GARDEE comme stockage(). Bloc absent = aucune purge,
    retro-compatible."""
    try:
        c = instance().get("conservation")
    except (OSError, ValueError, KeyError):
        return {}
    return c if isinstance(c, dict) else {}


def _donnees():
    """LE seul endroit qui decide ou vit data/. Priorite :

      1. env DONNEES        -- la ligne de lancement l'emporte toujours (compat :
                               les tests et les instances lancees a la main)
      2. stockage.chemin    -- mode « dossier », replique par le client de sync
      3. a cote de config/  -- l'instance porte ses donnees (CONFIG_DIR pose)
      4. RACINE / data      -- le depot lui-meme (dev, demo)
    """
    if env := os.environ.get("DONNEES"):
        return Path(env)
    s = stockage()
    if s.get("mode") == "dossier" and isinstance(s.get("chemin"), str) and s["chemin"].strip():
        return Path(s["chemin"].strip())
    if os.environ.get("CONFIG_DIR"):
        return CLIENT.parent / "data"
    return RACINE / "data"


# Fige au demarrage : deplacer les donnees d'une instance qui tourne n'est pas
# un rechargement a chaud, c'est un redemarrage (et un deplacement de fichiers).
DONNEES = _donnees()


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


def groupe(cle):
    """Tete de l'arborescence des dossiers salaries : societes.json ->
    etablissement.groupe, a defaut le nom de la societe."""
    s, e = etablissement(cle)
    return e.get("groupe") or s["nom"]


def manager(cle):
    """Adresse fixe du manager de l'etablissement (societes.json), ou ""."""
    _, e = etablissement(cle)
    return e.get("manager_email", "")


def url_publique():
    """Base des liens fabriques hors requete (recap.py) : instance.json -> url."""
    return (instance().get("url") or "").rstrip("/")


def mode_contrat(cle):
    """Couloir du schema, en donnee : 'genere' (Dark Kitchen) ou 'depose'
    (Restaurant, contrat fait a la main sur myrhis). Absent = 'genere' -- y
    compris quand l'etablissement lui-meme a disparu de la config (dossier
    vieux ou purge) : un ecran dossier ne doit pas 500 pour ca."""
    try:
        _, e = etablissement(cle)
    except KeyError:
        return "genere"
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
    saisit a la generation du contrat. Noms seuls ; voir saisie_rh_champs."""
    return [c["placeholder"] for c in saisie_rh_champs()]


def saisie_rh_champs():
    """Chaque entree de saisie_rh est soit "Placeholder", soit
    {"placeholder", "libelle", "aide"} -> toujours rendue sous la forme dict,
    libelle par defaut = nom du placeholder."""
    out = []
    for c in instance().get("saisie_rh", []):
        if isinstance(c, str):
            c = {"placeholder": c}
        out.append({"placeholder": c["placeholder"],
                    "libelle": c.get("libelle") or c["placeholder"],
                    "aide": c.get("aide", "")})
    return out


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


def _verifier_stockage():
    """Ou vivent les donnees, verifie comme le reste de la config.

    En mode « dossier », l'ECRITURE est testee pour de vrai : un dossier de
    synchronisation pas encore replique, deplace, ou monte en lecture seule doit
    etre refuse au demarrage -- pas decouvert a la premiere soumission d'un vrai
    salarie, quand la piece d'identite est deja partie dans le vide.

    Bloc absent = local : les instances installees avant ce bloc demarrent."""
    s = instance().get("stockage")
    if s is None:
        return {}
    if not isinstance(s, dict):
        return {"stockage": ["attend un objet, ex. {\"mode\": \"local\"}"]}
    mode = s.get("mode", "local")
    if mode == "local":
        return {}
    if mode != "dossier":
        return {"stockage": [f"mode « {mode} » inconnu — au choix : "
                             "« local » (data/ dans l'instance) ou « dossier » "
                             "(dossier répliqué par un client de synchronisation)"]}
    chemin = s.get("chemin")
    if not isinstance(chemin, str) or not chemin.strip():
        return {"stockage": ["mode « dossier » sans « chemin » : indiquez la racine "
                             "du dossier répliqué par le client de synchronisation"]}
    racine = Path(chemin.strip())
    if not racine.is_dir():
        return {"stockage": [f"« {chemin} » n'est pas un dossier — le client de "
                             "synchronisation est-il installé et le dossier répliqué ?"]}
    sonde = racine / f".contratgen-ecriture-{os.getpid()}"
    try:
        sonde.write_bytes(b"")
        sonde.unlink()
    except OSError as e:
        return {"stockage": [f"« {chemin} » n'est pas accessible en écriture : {e}"]}
    return {}


def _verifier_conservation():
    """Conservation des donnees : au bout de combien de jours un dossier termine
    voit ses pieces effacees. Patron de _verifier_stockage -- la FORME avant les
    valeurs, sinon c'est le garde-fou qui plante sur une config malformee.

    Bloc absent = aucune purge : les instances installees avant ce bloc
    demarrent sans rien changer."""
    c = instance().get("conservation")
    if c is None:
        return {}
    if not isinstance(c, dict):
        return {"conservation": ["attend un objet, ex. {\"jours\": 1095, "
                                 "\"jours_candidature\": 730, \"apres\": "
                                 "[\"RemisComptable\", \"Rejetee\", \"Abandonnee\"]}"]}
    import store          # store importe config : import local, cycle sinon
    raisons = []
    for cle in ("jours", "jours_candidature"):
        v = c.get(cle)
        # isinstance(True, int) est vrai -- refuser bool explicitement.
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raisons.append(f"« {cle} » attend un entier > 0 (reçu : {v!r})")
    apres = c.get("apres")
    if (not isinstance(apres, list) or not apres
            or not all(isinstance(e, str) for e in apres)):
        raisons.append("« apres » attend une liste non vide d'états")
    elif inconnus := [e for e in apres if e not in store.ETATS]:
        raisons.append("« apres » : état(s) inconnu(s) " + ", ".join(inconnus)
                       + " — au choix : " + ", ".join(store.ETATS))
    return {"conservation": raisons} if raisons else {}


def _verifier_installation():
    from werkzeug.security import check_password_hash
    manques = {}
    if SECRET_A_INSTALLER in instance().get("secret", ""):
        manques["secret"] = [f"encore « {SECRET_A_INSTALLER} » — lancez : "
                             f"python installer.py \"<Client>\""]
    # _verifier_comptes valide deja la FORME ; ici on garde quand meme les
    # isinstance -- verifier() lance tous les checks, celui-ci tourne meme si
    # le bloc utilisateurs est malforme, et ne doit pas planter dessus.
    users = comptes()
    if isinstance(users, dict) and any(
            isinstance(u, dict) and isinstance(u.get("mdp_hash"), str)
            and check_password_hash(u["mdp_hash"], MDP_PAR_DEFAUT)
            for u in users.values()):
        manques["compte RH"] = ["mot de passe par défaut — changez le hash dans "
                                "config/instance.json (bloc utilisateurs)"]
    if not etablissements():
        manques["établissements"] = ["aucun établissement dans config/societes.json"]
    # Sans destinataire RH, chaque soumission part en `mail_echoue` dans le
    # journal et personne ne le voit : deux instances de test ont servi comme
    # ca, verifier() disant OK. Un mail sans expediteur est refuse par un
    # relais SMTP reel, autant le dire avant le passage en mode smtp.
    m = instance().get("mails") or {}
    if not [d for d in (m.get("rh") or []) if d]:
        manques["mails"] = ["« rh » vide : aucune adresse ne recevrait les demandes "
                            "à valider — renseignez mails.rh dans instance.json"]
    if not (m.get("expediteur") or "").strip():
        manques.setdefault("mails", []).append(
            "« expediteur » vide : renseignez l'adresse d'envoi dans mails")
    # Le mail hebdo au cabinet est fabrique hors requete (cron) : sans base
    # d'URL il partirait avec des liens vides, et personne ne lit la sortie
    # d'un cron -- meme trou que mails.rh, refuse au meme endroit.
    if not url_publique():
        manques["url"] = ["vide : l'adresse publique de l'app (ex. https://embauche.acme.fr), "
                          "base des liens du mail hebdomadaire au cabinet"]
    return manques


def _verifier_comptes():
    """Forme de comptes() avant ses valeurs -- un bloc `utilisateurs` malforme
    dans instance.json doit refuser de demarrer proprement, pas planter login()."""
    d = comptes()
    if not isinstance(d, dict) or not d:
        return {"comptes": ["aucun compte de connexion — lancez : "
                            "python installer.py \"<Client>\""]}
    raisons = [f"« {ident} » : mdp_hash absent ou invalide"
               for ident, u in d.items()
               if not isinstance(u, dict) or not isinstance(u.get("mdp_hash"), str)
               or not u["mdp_hash"]]
    return {"comptes": raisons} if raisons else {}


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


def _verifier_grille():
    """Chaque poste du formulaire doit avoir SA ligne dans grille.json.

    La grille reconnait un poste par mots normalises, la plus specifique
    gagne -- souple exprès (« Equipier Polyvalent » prend le tarif
    « Equipier »), mais un poste PRIVE de sa ligne tombe alors en silence sur
    celle d'un poste dont l'intitule est contenu dans le sien : « Apprenti
    couvreur » sort au tarif « Couvreur ». Le contrat n'est pas vide, il est
    FAUX -- invisible pour doctor comme pour la RH, et sur le seul champ dont
    personne ne doute. Deux postes qui visent la meme ligne sont donc refuses
    ici : si le partage est voulu, deux lignes de meme montant le disent.
    """
    g = grille()
    if not g:
        return {}
    import contrat
    # La FORME avant les valeurs : sans ca, une ligne sans « poste » fait
    # planter le garde-fou (KeyError) au lieu d'etre refusee -- et le meme
    # acces non garde est dans contrat.ligne_grille().
    lignes = g.get("postes")
    if not isinstance(lignes, list) or not all(
            isinstance(e, dict) and isinstance(e.get("poste"), str) for e in lignes):
        return {"grille.json": ["« postes » attend une liste d'objets portant "
                                "chacun un « poste » (texte)"]}
    manques, pris = {}, {}
    for c in champs():
        if c["id"] != role("poste"):
            continue
        for opt in c.get("options", []):
            ligne = contrat.ligne_grille(opt, g)
            if ligne is None:
                manques[f"grille — poste « {opt} »"] = [
                    "aucune ligne de config/grille.json ne le rémunère"]
            elif (autre := pris.get(ligne["poste"])) is not None:
                # On ne sait pas lequel des deux est le proprietaire legitime de
                # la ligne (ca depend de l'ordre des options) : nommer les deux
                # plutot que d'accuser au hasard celui vu en second.
                manques[f"grille — postes « {autre} » et « {opt} »"] = [
                    f"partagent la ligne « {ligne['poste']} » de config/grille.json "
                    f"— l'un des deux doit avoir sa propre ligne (même montant si "
                    f"le partage est voulu)"]
            else:
                pris[ligne["poste"]] = opt
    return manques


def verifier():
    """Garde-fou de demarrage. Renvoie {} si tout va bien, sinon un dict
    {sujet: [raisons]} et l'instance ne sert pas."""
    manques = {}
    for check in (_verifier_version, _verifier_comptes, _verifier_stockage,
                  _verifier_conservation, _verifier_installation,
                  _verifier_derives, _verifier_roles, _verifier_placeholders,
                  _verifier_postes, _verifier_grille):
        manques.update(check())
    return manques
