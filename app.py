"""Application : le parcours formulaire -> validation -> dossier -> contrat ->
DPAE -> remise au cabinet comptable.

Aucune action au GET : chaque decision passe par un ecran de confirmation
puis un POST (ticket 08).
"""
import io
import json
import mimetypes
import os
import socket
import threading
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

import config
import contrat
import mails
import signature
import store


def _jours_ouvrables_entre(de, a):
    """Nombre de jours ouvrables (lun-ven, hors jours feries) entre `de` et `a`.
    Positif si `a` est apres `de`, negatif sinon. Pas de gestion des jours
    feries — c'est une estimation conservatrice pour un avertissement, pas un
    calcul juridique."""
    from datetime import timedelta
    total = 0
    signe = 1 if a >= de else -1
    courant = de
    while courant != a:
        courant += timedelta(days=signe)
        if courant.weekday() < 5:           # lun=0 .. ven=4
            total += 1
    return total * signe

CONTRAT_EXT = {".pdf", ".docx"}          # contrat venu de myrhis / PDF signé

# --- rate-limit par IP ----------------------------------------------------
# Deux pots : le formulaire public (seul endpoint non authentifie qui ecrit sur
# disque) et /login. Pas de captcha (le vrai destinataire du formulaire est un
# manager, pas un spammeur), mais un minimum de garde-fou contre bots et flood.
#
# MONO-PROCESS IMPOSE : ces pots -- comme store._verrou -- vivent en memoire.
# Sous plusieurs process les plafonds seraient divises d'autant, et pire,
# store._verrou cesserait d'exclure quoi que ce soit (perte d'entrees de
# journal). C'est pourquoi `python app.py` demarre waitress, mono-process par
# construction (voir le bas de ce fichier). Attention : `app` reste un objet
# WSGI importable, donc `gunicorn app:app` ou `waitress-serve --processes=N`
# cassent l'invariant SANS RIEN SIGNALER -- il est tenu par la facon de
# lancer, pas par le code. Passer a plusieurs process exige d'abord un verrou
# fichier dans store et un compteur partage ici.
# Voir le meme avertissement a store._verrou.
_POT_PAR_IP = defaultdict(list)          # formulaire public : {ip: [timestamps]}
_FENETRE = 300                           # 5 minutes
_PLAFOND = 10                            # max soumissions / fenetre

_POT_LOGIN = defaultdict(list)           # /login : {ip: [timestamps]}
_FENETRE_LOGIN = 900                     # 15 minutes
_PLAFOND_LOGIN = 10                      # max tentatives / fenetre


def _autorise(pot, cle, fenetre, plafond):
    """Rate-limit glissant en memoire. Pas de persistence — un redemarrage
    remet le compteur a zero, c'est acceptable."""
    now = time.time()
    pot[cle] = [t for t in pot[cle] if now - t < fenetre]
    if len(pot[cle]) >= plafond:
        return False
    pot[cle].append(now)
    return True


def _soumission_autorisee(ip):
    return _autorise(_POT_PAR_IP, ip, _FENETRE, _PLAFOND)

def _pot_rempli():
    """Honeypot : champ invisible que les bots remplissent mais pas les humains."""
    return bool(request.form.get("website"))

app = Flask(__name__)
# X-Forwarded-For n'est digne de confiance QUE s'il est pose par un proxy a
# nous. Sans proxy devant, c'est un en-tete fourni par le client : le faire
# tourner contournerait entierement les deux rate-limits ci-dessus (verifie).
# PROXIES declare donc le nombre REEL de proxies devant l'app -- 0 par defaut
# (`python app.py` en direct), 1 derriere Caddy, 2 si un CDN s'ajoute devant.
# Trop haut = remote_addr forgeable derriere un proxy qui AJOUTE a l'en-tete du
# client (nginx $proxy_add_x_forwarded_for) ; derriere Caddy, qui l'ecrase, on
# retombe sur l'IP du proxy. Trop bas = tous les visiteurs partagent le meme
# compteur et la Nieme requete legitime est jetee en silence.
try:
    _PROXIES = int(os.environ.get("PROXIES", 0))
except ValueError:
    raise SystemExit("PROXIES doit etre un entier : 0 en direct, 1 derriere "
                     "Caddy, 2 avec un CDN devant. Recu : "
                     + repr(os.environ["PROXIES"]))
if _PROXIES:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_PROXIES, x_proto=_PROXIES)
app.secret_key = config.instance()["secret"]
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # SameSite=Lax n'est PAS cosmetique : c'est la seule protection CSRF de
    # l'app. Toutes les routes mutantes sont des POST derriere @rh, et rien
    # d'autre ne verifie l'origine — un POST cross-site n'emporte pas le
    # cookie grace a cette ligne. Ne pas la retirer en croyant nettoyer.
    SESSION_COOKIE_SAMESITE="Lax",
    # Defaut sur : le dev local en http passe par `DEBUG=1 python app.py`,
    # deja l'incantation documentee plus bas.
    SESSION_COOKIE_SECURE=not os.environ.get("DEBUG"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


def _nom(champs):
    """Nom affiche du salarie (suivi, mails, journal). Formulaire a champs
    separes -> « Prenom NOM » ; formulaire a champ unique -> le champ designe
    par le role 'nom'."""
    return " ".join(p for p in config.identite(champs) if p)


def _email_demandeur(champs):
    return config.valeur(champs, "email")


def _mail(uid, modele, a, **vals):
    """Envoie, et JOURNALISE l'echec. -> True si parti.

    Un mail perdu ne doit jamais etre silencieux : c'est lui qui porte le lien
    de correction du manager ou l'avis au cabinet comptable. En mode console
    l'echec creve les yeux, mais une instance client tourne en SMTP et personne
    ne regarde sa sortie standard -- la trace doit etre dans le dossier."""
    ok, raison = mails.envoyer(modele, a, **vals)
    if not ok:
        store.noter(uid, type="mail_echoue", par="systeme",
                    modele=modele, motif=raison)
    return ok


# Libelles et tons d'affichage des etats -- presentation seule, l'etat
# machine reste celui de store.ETATS.
LIBELLES_ETAT = {"Soumise": "Soumise", "Rejetee": "Rejetée",
                 "ATraiter": "À traiter", "ContratPret": "Contrat prêt",
                 "ContratSigne": "Contrat signé",
                 "DpaeFaite": "DPAE faite", "RemisComptable": "Remis au comptable",
                 "Abandonnee": "Abandonnée", "Parti": "Ancien salarié"}
TONS_ETAT = {"Soumise": "attente", "ATraiter": "attente", "Rejetee": "ko",
             "ContratPret": "actif", "ContratSigne": "actif",
             "DpaeFaite": "actif",
             "RemisComptable": "fini", "Abandonnee": "", "Parti": ""}


def _depuis(iso):
    """Jours ecoules depuis un horodatage ISO du journal (derniere activite)."""
    d = datetime.fromisoformat(iso)
    return (datetime.now(d.tzinfo) - d).days


def _date_fr(iso):
    """AAAA-MM-JJ -> JJ/MM/AAAA. Valeur non datee : rendue telle quelle."""
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso or ""


@app.context_processor
def _aides():
    return {"nom_de": _nom,
            # Les ecrans lisaient « item.champs.poste » en dur : chez un client
            # qui renomme ses champs, la colonne sortait VIDE au lieu de crier.
            "valeur": config.valeur,
            "depuis": _depuis,
            "date_fr": _date_fr,
            "libelle_etat": lambda e: LIBELLES_ETAT.get(e, e),
            "ton_etat": lambda e: TONS_ETAT.get(e, "")}


@app.context_processor
def globaux():
    return {"client": config.instance()["client"],
            "schema": config.formulaire(),
            "etablissements": config.etablissements()}


# --- session RH ----------------------------------------------------------

def rh(vue):
    @wraps(vue)
    def garde(*a, **kw):
        if "utilisateur" not in session:
            return redirect(url_for("login", suite=request.path))
        return vue(*a, **kw)
    return garde


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Budget consomme a CHAQUE POST, pas seulement aux echecs : ne compter
        # que les echecs imposerait de separer lecture et ecriture du compteur.
        # Par IP seulement — avec un compte unique, un compteur par identifiant
        # serait global, donc un levier de deni de service contre la seule
        # utilisatrice, pour zero gain defensif.
        if not _autorise(_POT_LOGIN, request.remote_addr,
                         _FENETRE_LOGIN, _PLAFOND_LOGIN):
            flash("Trop de tentatives. Réessayez dans quelques minutes.", "erreur")
            return render_template("login.html"), 429
        u = config.comptes().get(request.form.get("identifiant", ""))
        if u and check_password_hash(u["mdp_hash"], request.form.get("mot_de_passe", "")):
            # Sans `permanent`, PERMANENT_SESSION_LIFETIME ne fait rien du tout.
            session.permanent = True
            session["utilisateur"] = request.form["identifiant"]
            return redirect(request.args.get("suite") or url_for("suivi"))
        flash("Identifiant ou mot de passe incorrect.", "erreur")
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.post("/recharger")
@rh
def recharger():
    """Relit config/ sans redemarrer : nouvel etablissement, salaire revalorise,
    clause modifiee. C'est l'editeur qui edite les .json (pas d'ecran d'admin),
    mais plus besoin d'un acces au serveur du client pour redemarrer.

    Une config invalide ne prend pas : l'ancienne reste active et on dit ce qui
    cloche -- une instance qui servait continue de servir."""
    if manques := config.recharger():
        for sujet, quoi in manques.items():
            flash(f"{sujet} : {', '.join(quoi)}", "erreur")
        flash("Configuration NON rechargée — la précédente reste active.", "erreur")
        return redirect(url_for("suivi"))
    # Le secret sert AUSSI a signer les cookies de session, et celui-la est fige
    # a l'import : sans cette ligne, un secret change laisserait store.signer sur
    # le nouveau et les cookies sur l'ancien -- liens du lot casses en silence.
    # Un secret reellement change deconnecte tout le monde, c'est voulu.
    app.secret_key = config.instance()["secret"]
    # Meme piege que le secret, autre issue : config.DONNEES est fige a l'import
    # et deplacer les donnees n'est pas un rechargement (il faut aussi deplacer
    # les fichiers). On refuse de faire semblant : on le dit.
    if config._donnees() != config.DONNEES:
        flash(f"Le stockage a changé ({config._donnees()}), mais les données "
              f"continuent d'aller dans {config.DONNEES} : redémarrez l'app "
              f"après avoir déplacé les fichiers.", "erreur")
    flash("Configuration rechargée.", "ok")
    return redirect(url_for("suivi"))


# --- formulaire public ---------------------------------------------------

def _saisie(item=None):
    """Lit et valide le formulaire contre le schema. -> (champs, erreurs).

    `item` = la soumission en cours de correction : une piece deja deposee
    n'est pas redemandee, seule celle qu'on remplace est relue.
    """
    champs, erreurs = {}, []
    deja = set() if item is None else (
        {c["role"] for c in config.pieces()}
        - {m["champ"]["role"] for m in store.manquantes(item)})
    for c in config.champs():
        if c["type"] == "piece_jointe":
            max_f = c.get("max_fichiers", 1)
            liste = [f for f in request.files.getlist(c["id"]) if f and f.filename]
            if c.get("requis") and not liste and c["role"] not in deja:
                erreurs.append(f"« {c['libelle']} » est obligatoire"
                               + (f" ({max_f} fichiers attendus)." if max_f > 1 else "."))
            elif (c.get("requis") and c["role"] not in deja
                  and 0 < len(liste) < max_f):
                erreurs.append(f"« {c['libelle']} » : {max_f} fichiers attendus "
                                "(recto + verso).")
            elif len(liste) > max_f:
                erreurs.append(f"« {c['libelle']} » : maximum {max_f} fichiers.")
            # Format verifie ICI, avant toute ecriture : sinon store.deposer leve
            # en plein creer_soumission -> 500 sur le formulaire public (vecu
            # avec un diplome envoye en .docx).
            for f in liste:
                ext = os.path.splitext(f.filename)[1].lower()
                if ext not in store.EXTENSIONS:
                    erreurs.append(f"« {c['libelle']} » : format {ext or 'sans extension'} "
                                   "refusé — " + ", ".join(sorted(store.EXTENSIONS)) + ".")
            continue
        v = (request.form.get(c["id"]) or "").strip()
        champs[c["id"]] = v
        exige = c.get("requis")
        if c.get("requis_si"):
            autre, attendu = c["requis_si"]
            exige = (request.form.get(autre) or "").strip() == attendu
        if exige and not v:
            erreurs.append(f"« {c['libelle']} » est obligatoire.")
        if v and c["type"] == "etablissement":
            if v not in dict(config.etablissements()):
                erreurs.append("Établissement inconnu.")
    return champs, erreurs


# Le formulaire public est soumis en fetch() (cf. formulaire.html) : quand un
# champ est invalide, la page N'EST PAS rechargée, les pièces déjà choisies
# restent dans le navigateur de la personne — rien n'est écrit côté serveur tant
# que la soumission n'aboutit pas. Ces réponses parlent alors JSON. Sans JS, le
# POST classique fonctionne encore (les pièces sont reperdues à l'erreur).
def _est_fetch():
    return request.headers.get("X-Requested-With") == "contratgen-fetch"


def _formulaire_ko(champs, erreurs, **gabarit):
    if _est_fetch():
        return {"ok": False, "erreurs": erreurs}, 422
    return render_template("formulaire.html", champs=champs, erreurs=erreurs, **gabarit)


def _formulaire_ok(titre, texte):
    if _est_fetch():
        return {"ok": True, "titre": titre, "texte": texte}
    return render_template("message.html", titre=titre, texte=texte)


@app.route("/", methods=["GET", "POST"])
def formulaire():
    if request.method == "POST":
        if _pot_rempli() or not _soumission_autorisee(request.remote_addr):
            return _formulaire_ok("Demande envoyée", "Votre demande est bien "
                                  "enregistrée. Le service RH la traite et vous "
                                  "recontacte si une pièce manque ou doit être "
                                  "complétée.")
        champs, erreurs = _saisie()
        if erreurs:
            return _formulaire_ko(champs, erreurs, action=url_for("formulaire"))
        try:                                      # reste : taille d'un fichier
            uid = store.creer_soumission(champs, request.files)
        except ValueError as e:
            return _formulaire_ko(champs, [str(e)], action=url_for("formulaire"))
        ok = _mail(uid, "nouvelle_soumission", config.instance()["mails"]["rh"],
                   nom=_nom(champs), id=uid,
                   lien=url_for("detail", uid=uid, _external=True))
        return _formulaire_ok("Demande envoyée",
                              "Votre demande est bien enregistrée. Le service RH la "
                              "traite et vous recontacte si une pièce manque ou doit "
                              "être complétée." if ok else
                              "Votre demande est bien enregistrée, mais l'avis au "
                              "service RH n'a pas pu partir : prévenez-le si vous "
                              "restez sans réponse.")
    return render_template("formulaire.html", champs={}, erreurs=[],
                           action=url_for("formulaire"))


@app.route("/corriger/<jeton>", methods=["GET", "POST"])
def corriger(jeton):
    """Lien signe sans compte : le tiers corrige SA soumission rejetee."""
    item = store.verifier_lien(jeton, "correction")
    if not item or store.etat(item) != "Rejetee":
        return render_template("message.html", titre="Lien invalide",
                               texte="Ce lien a expiré, a déjà servi, ou la "
                                     "demande n'attend plus de correction."), 410
    ko = item["journal"][-1]
    if request.method == "POST":
        if _pot_rempli() or not _soumission_autorisee(request.remote_addr):
            return _formulaire_ok("Correction envoyée",
                                  "Le service RH va réexaminer la demande.")
        champs, erreurs = _saisie(item)
        if erreurs:
            return _formulaire_ko(champs, erreurs, ko=ko, action=request.path)
        try:
            store.resoumettre(item["id"], champs, request.files)
        except ValueError as e:
            return _formulaire_ko(champs, [str(e)], ko=ko, action=request.path)
        ok = _mail(item["id"], "nouvelle_soumission",
                   config.instance()["mails"]["rh"], nom=_nom(champs),
                   id=item["id"],
                   lien=url_for("detail", uid=item["id"], _external=True))
        return _formulaire_ok("Correction envoyée",
                              "Le service RH va réexaminer la demande." if ok else
                              "Votre correction est bien enregistrée, mais l'avis au "
                              "service RH n'a pas pu partir : prévenez-le si vous "
                              "restez sans réponse.")
    return render_template("formulaire.html", champs=item["champs"], erreurs=[],
                           ko=ko, action=request.path)


# --- suivi ---------------------------------------------------------------

@app.get("/suivi")
@rh
def suivi():
    items = store.tout()
    etab = request.args.get("etablissement") or ""
    etat_f = request.args.get("etat") or ""
    # RemisComptable/Parti = procedure terminee -> vue /salaries, plus /suivi.
    en_cours = [i for i in items if store.etat(i) not in store.TERMINES]
    visibles = [i for i in en_cours
                if (not etab or config.valeur(i["champs"], "etablissement") == etab)
                and (not etat_f or store.etat(i) == etat_f)]
    return render_template("suivi.html", items=visibles, etat=store.etat,
                           inactifs=store.INACTIFS, total=len(en_cours),
                           purge_active=bool(config.conservation()),
                           a_purger=len(store.eligibles(items)),
                           etab=etab, etat_f=etat_f, libelles=LIBELLES_ETAT,
                           etats=[e for e in store.ETATS if e not in store.TERMINES],
                           manquantes=store.manquantes,
                           aujourdhui=date.today().isoformat(),
                           a_traiter=sum(store.etat(i) in ("Soumise", "ATraiter")
                                         for i in en_cours))


@app.get("/salaries")
@rh
def salaries():
    """Les salaries dont la procedure est allee au bout (RemisComptable), ou
    ceux partis (Parti), groupes par etablissement. Complement de /suivi
    (demandes en cours)."""
    anciens = request.args.get("anciens")
    cible = "Parti" if anciens else "RemisComptable"
    tous = [i for i in store.tout() if store.etat(i) == cible]
    etab = request.args.get("etablissement") or ""
    visibles = [i for i in tous
                if not etab or config.valeur(i["champs"], "etablissement") == etab]
    groupes = {}
    for i in visibles:
        groupes.setdefault(config.valeur(i["champs"], "etablissement") or "—", []).append(i)

    def cabinet(cle):
        try:
            return config.comptable(cle)
        except KeyError:                    # etablissement retire de la config
            return ""
    return render_template("salaries.html", groupes=sorted(groupes.items()),
                           total=len(tous), etab=etab, anciens=anciens,
                           remis_le=store.date_remise, date_sortie=store.date_sortie,
                           cabinet=cabinet, aujourdhui=date.today().isoformat())


@app.get("/dossier/<uid>")
@rh
def detail(uid):
    item = store.lire(uid) or abort(404)
    # La fiche salarié est stockée dans le bucket "pieces" (FICHE PERSONNELLE,
    # cf. _fiche_salarie), mais c'est un document PRODUIT par l'app : elle
    # s'affiche donc avec le contrat, pas avec les pièces jointes par le
    # candidat -- affichage seul, le stockage disque ne bouge pas.
    fiche = store.fichiers_role(item, "pieces", "fiche-salarie")
    return render_template("dossier.html", item=item, etat=store.etat(item),
                           pieces=[f for f in store.fichiers(item, "pieces")
                                  if f not in fiche],
                           produits=[(f, "contrat") for f in store.fichiers(item, "contrat")]
                                   + [(f, "pieces") for f in fiche],
                           manquantes=store.manquantes(item),
                           date_sortie=store.date_sortie(item),
                           motifs=config.instance()["motifs_ko"],
                           champ=config.champ, saisie_rh=config.saisie_rh_champs(),
                           nom_affiche=_nom(item["champs"]),
                           mode_contrat=config.mode_contrat(config.valeur(item["champs"], "etablissement")),
                           signature_esign=config.instance().get("signature", {})
                           .get("mode") == "yousign")


@app.after_request
def _entetes(reponse):
    """L'app sert des fichiers deposes par des tiers depuis un formulaire
    public. `nosniff` empeche le navigateur de requalifier un contenu en HTML
    et de l'executer sur l'origine de l'app."""
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    return reponse


# Le contrat genere est toujours un .docx (cf. contrat.generer), mais il porte
# des valeurs venues du formulaire public : on ne le sert jamais inline. Un
# contrat se telecharge, il ne se previsualise pas ; les pieces (pdf/jpg/png)
# restent en apercu.
def _servir(octets, nom, bucket):
    if octets is None:
        abort(404)
    return send_file(io.BytesIO(octets), download_name=nom,
                     as_attachment=(bucket == "contrat"),
                     mimetype=mimetypes.guess_type(nom)[0] or "application/octet-stream")


@app.get("/dossier/<uid>/fichier/<bucket>/<nom>")
@rh
def fichier(uid, bucket, nom):
    if bucket not in ("pieces", "contrat"):
        abort(404)
    return _servir(store.ouvrir(uid, bucket, nom), nom, bucket)


# --- actions RH ----------------------------------------------------------

def _transition(uid, vers, **extra):
    """store.transition avec le garde de store.TRANSITIONS rendu en flash au
    lieu d'un 500 : un POST hors séquence est refusé proprement."""
    try:
        return store.transition(uid, vers, session["utilisateur"], **extra)
    except ValueError as e:
        flash(str(e), "erreur")
        return None


def _pieces_du_dossier(item):
    """{{PiecesFournies}} / {{PiecesManquantes}} pour ce dossier.

    placeholders_connus() les annonce au client (PLACEHOLDERS.md), donc ils
    peuvent tomber dans N'IMPORTE quel modele -- contrat comme fiche. Les
    calculer a un seul endroit : la fiche les fournissait, le contrat non, et
    doctor les injectant de son cote, un contrat qui les citait passait tous
    les garde-fous puis sortait « non genere » devant la RH."""
    fournies = [c["libelle"] for c in config.pieces()
                if store.fichiers_role(item, "pieces", c["role"])]
    manquantes = [m["champ"]["libelle"] for m in store.manquantes(item)]
    return {"PiecesFournies": ", ".join(fournies) or "—",
            "PiecesManquantes": ", ".join(manquantes) or "aucune"}


def _fiche_salarie(item):
    """Génère la fiche salarié dans pieces/ (FICHE PERSONNELLE) : le modèle du
    CLIENT s'il en déclare un, sinon le modèle générique versé avec le code --
    elle est désormais TOUJOURS produite. Elle décrit le salarié, pas
    l'engagement contractuel. Le schéma la place à l'ouverture du dossier. Un
    échec n'annule pas la validation : on note et on continue."""
    def echec(motif):
        store.noter(item["id"], type="fiche_echouee", par="systeme", motif=motif)
        return False

    # KeyError attrape SEUL config.mentions(), et rien d'autre : l'etablissement
    # du dossier peut avoir ete retire de societes.json depuis la soumission, et
    # la fiche est desormais produite pour TOUS les clients (avant, ceux sans
    # modele sortaient plus haut). Hors try, ce KeyError faisait un 500 sur
    # /valider APRES l'ecriture de la transition : dossier valide, mais ni fiche,
    # ni contrat, ni DPAE. Envelopper tout le bloc ferait passer un KeyError venu
    # d'ailleurs pour un etablissement manquant -- motif mensonger au journal.
    try:
        mentions = config.mentions(config.valeur(item["champs"], "etablissement"))
    except KeyError as e:
        return echec(f"établissement {e} absent de config/societes.json")
    try:
        vals = contrat.valeurs(item["champs"], mentions,
                               extra=_pieces_du_dossier(item))
        modele, generique = config.fiche_salarie()
        store.poser_octets(item["id"], "pieces",
                           store.nom_piece(item, "fiche-salarie", ".docx"),
                           contrat.generer(modele, vals, elaguer_lignes=generique))
        store.noter(item["id"], type="fiche_salarie", par="systeme")
        return True
    except (ValueError, OSError) as e:
        return echec(str(e))


@app.post("/dossier/<uid>/valider")
@rh
def valider(uid):
    try:
        store.valider(uid, session["utilisateur"])
    except ValueError as e:                       # déjà un dossier (double-clic / course)
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    item = store.lire(uid)
    ok_fiche = _fiche_salarie(item)
    ok_dpae = _rappel_dpae(item)

    flash("Dossier salarié ouvert.", "popup")
    flash("Fiche salarié générée → section « Documents produits »" if ok_fiche
          else "Fiche salarié NON générée (voir le journal)",
          "popup" if ok_fiche else "popup-erreur")

    # Contrat produit dès la validation — plus de bouton « Générer » à part.
    # Sauf couloir « déposé » (contrat fait sur myrhis) ou config qui réclame
    # une saisie RH avant génération : l'écran ATraiter garde alors son bouton,
    # et la pop-up ne doit pas prétendre qu'un contrat est sorti.
    if (config.mode_contrat(config.valeur(item["champs"], "etablissement")) == "genere"
            and not config.saisie_rh()):
        ok_c, msg = _produire_contrat(uid)
        flash("Contrat généré → section « Documents produits »" if ok_c
              else f"Contrat NON généré ({msg})",
              "popup" if ok_c else "popup-erreur")
    else:
        flash("Contrat à déposer → section « Documents produits »", "popup")

    flash("Rappel DPAE envoyé" if ok_dpae else "Rappel DPAE NON parti (voir le journal)",
          "popup" if ok_dpae else "popup-erreur")

    return redirect(url_for("detail", uid=uid))


def _rappel_dpae(item):
    """Effet de la validation, pas un etat : le rappel part dans la meme requete
    (pas de cron = pas de second process ecrivain, cf. store._verrou), avec les
    donnees que la DPAE reclame et qui sont deja dans le dossier."""
    conf = config.instance()["mails"]
    champs = item["champs"]
    try:
        m = config.mentions(config.valeur(champs, "etablissement"))
    except KeyError:
        m = {}
    return _mail(item["id"], "rappel_dpae", conf.get("dpae") or conf["rh"],
                 nom=_nom(champs),
                 debut=_date_fr(config.valeur(champs, "date_debut")) or "—",
                 poste=config.valeur(champs, "poste"),
                 societe=m.get("Societe", ""), etablissement=m.get("Etablissement", ""),
                 siret=m.get("Siret", ""),
                 lien=url_for("detail", uid=item["id"], _external=True))


@app.post("/dossier/<uid>/rejeter")
@rh
def rejeter(uid):
    motif = request.form.get("motif")
    commentaire = (request.form.get("commentaire") or "").strip()
    if motif not in config.instance()["motifs_ko"] or not commentaire:
        flash("Un motif et un commentaire sont obligatoires pour un KO.", "erreur")
        return redirect(url_for("detail", uid=uid))
    store.rejeter(uid, motif, commentaire, session["utilisateur"])
    item = store.lire(uid)
    lien = url_for("corriger", jeton=store.signer("correction", uid, item["link_epoch"]),
                   _external=True)
    # Le lien de correction part a l'adresse FIXE du manager de l'etablissement
    # (societes.json), pas a celle tapee dans le formulaire public ; l'email
    # saisi ne sert que de repli pour un etablissement qui n'en declare pas.
    a = (config.manager(config.valeur(item["champs"], "etablissement"))
         or _email_demandeur(item["champs"]))
    ok = _mail(uid, "rejet", a, motif=motif, commentaire=commentaire, lien=lien,
               nom=_nom(item["champs"]))
    flash("Demande rejetée, lien de correction envoyé." if ok
          else "Demande rejetée, mais le mail n'est pas parti : le manager n'a "
               "PAS reçu le lien de correction — voir le journal.",
          "ok" if ok else "erreur")
    return redirect(url_for("detail", uid=uid))


def _produire_contrat(uid, extra=None):
    """Génère le contrat .docx depuis le modèle du poste et passe le dossier à
    ContratPret. -> (True, None) ou (False, raison). Appelé à la validation
    (auto) et par le bouton « Générer » (config à saisie RH, ou reprise après
    un échec : modèle manquant, placeholder critique vide)."""
    item = store.lire(uid)
    if config.mode_contrat(config.valeur(item["champs"], "etablissement")) != "genere":
        return False, ("établissement en contrat déposé (myrhis) : "
                       "utilisez « Déposer le contrat »")
    modele = config.modele_pour(item["champs"])
    if not modele:
        return False, (f"aucun modèle configuré pour le poste "
                       f"« {config.valeur(item['champs'], 'poste')} »")
    # Placeholders qu'aucune question du formulaire ne fournit : saisis par la RH,
    # fusionnes dans champs pour que la regeneration et le lot les voient.
    extra = {k: (v or "").strip() for k, v in (extra or {}).items()}
    if extra:
        item = store.completer_champs(uid, extra)
    # Les listes de pieces sont CALCULEES, pas saisies : elles rejoignent les
    # valeurs du rendu, jamais completer_champs (qui fige la saisie RH dans le
    # dossier). Meme source que la fiche salarie, sinon un contrat qui cite
    # {{PiecesFournies}} sort « non genere » alors que doctor l'a validé.
    extra = {**extra, **_pieces_du_dossier(item)}
    # KeyError attrape SEUL config.mentions() : l'etablissement du dossier peut
    # avoir ete retire de societes.json depuis la soumission, et le 500 tombait
    # APRES l'ecriture de la transition. Envelopper tout le bloc ferait passer un
    # KeyError venu d'ailleurs pour un etablissement manquant.
    try:
        mentions = config.mentions(config.valeur(item["champs"], "etablissement"))
    except KeyError as e:
        return False, f"établissement {e} absent de config/societes.json"
    # OSError compris : la génération est un EFFET de la validation (comme
    # _fiche_salarie, qui garde le même couple) -- un disque plein ne doit pas
    # renvoyer un 500 alors que le dossier est déjà ouvert.
    try:
        vals = contrat.valeurs(item["champs"], mentions, extra=extra)
        octets = contrat.generer(config.CLIENT / "contrats" / modele, vals)
        store.poser_octets(uid, "contrat", store.nom_piece(item, "contrat", ".docx"), octets)
    except (ValueError, OSError) as e:
        return False, str(e)
    _transition(uid, "ContratPret", modele=modele)
    _alerte_cdd(item)
    return True, None


def _alerte_cdd(item):
    """Avertissement legal CDD : la remise doit intervenir dans les 2 jours
    ouvrables suivant la date de debut."""
    if config.valeur(item["champs"], "type_contrat") != "CDD":
        return
    try:
        debut = date.fromisoformat(config.valeur(item["champs"], "date_debut"))
    except (ValueError, TypeError):
        return
    reste = _jours_ouvrables_entre(date.today(), debut)
    if reste < 0:
        flash(f"Attention : la date de début est dépassée de {-reste} "
              f"jour(s) ouvrable(s).", "erreur")
    elif reste <= 2:
        flash(f"Attention : il reste {reste} jour(s) ouvrable(s) avant la date "
              f"de début — la remise au salarié doit intervenir dans les 2 jours "
              f"ouvrables.", "erreur")


@app.post("/dossier/<uid>/contrat")
@rh
def generer_contrat(uid):
    store.lire(uid) or abort(404)
    ok, msg = _produire_contrat(uid, {k: request.form.get(k)
                                      for k in config.saisie_rh()})
    flash("Contrat généré." if ok else f"Contrat non généré : {msg}.",
          "ok" if ok else "erreur")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/contrat-depose")
@rh
def contrat_depose(uid):
    """Couloir Restaurant : le contrat est fait à la main sur myrhis, la RH
    dépose le PDF/.docx ici."""
    item = store.lire(uid) or abort(404)
    if config.mode_contrat(config.valeur(item["champs"], "etablissement")) != "depose":
        flash("Cet établissement génère son contrat : utilisez « Générer ».", "erreur")
        return redirect(url_for("detail", uid=uid))
    f = request.files.get("contrat")
    if not (f and f.filename):
        flash("Le fichier du contrat est obligatoire.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(uid, "contrat", "contrat", f, extensions=CONTRAT_EXT)
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _transition(uid, "ContratPret", modele="(déposé)")
    flash("Contrat déposé.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/contrat-signe")
@rh
def contrat_signe(uid):
    """Signature manuelle : la RH dépose le contrat signé hors app."""
    store.lire(uid) or abort(404)
    f = request.files.get("signe")
    if not (f and f.filename):
        flash("Le contrat signé est obligatoire.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(uid, "contrat", "contrat-signe", f, extensions=CONTRAT_EXT)
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _transition(uid, "ContratSigne")
    flash("Contrat signé enregistré.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/signature-envoyer")
@rh
def signature_envoyer(uid):
    item = store.lire(uid) or abort(404)
    contrats = store.fichiers(item, "contrat")
    src = next((c for c in contrats if store._extraire_role(c) == "contrat"), None)
    octets = store.ouvrir(item["id"], "contrat", src) if src else None
    if octets is None:
        flash("Aucun contrat à envoyer.", "erreur")
        return redirect(url_for("detail", uid=uid))
    c = item["champs"]
    try:
        pid = signature.envoyer(
            octets,
            {"prenom": config.valeur(c, "prenom"),
             "nom": _nom(c) or "",
             "email": _email_demandeur(c)})
    except (RuntimeError, KeyError, OSError) as e:
        flash(f"Envoi à la signature échoué : {e}", "erreur")
        return redirect(url_for("detail", uid=uid))
    store.noter(uid, type="signature_envoyee", par=session["utilisateur"], procedure=pid)
    flash("Contrat envoyé à la signature.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/signature-verifier")
@rh
def signature_verifier(uid):
    item = store.lire(uid) or abort(404)
    pid = next((e["procedure"] for e in reversed(item["journal"])
                if e.get("type") == "signature_envoyee"), None)
    if not pid:
        flash("Aucune procédure de signature en cours.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        pdf = signature.recuperer(pid)
    except (RuntimeError, OSError) as e:
        flash(f"Vérification échouée : {e}", "erreur")
        return redirect(url_for("detail", uid=uid))
    if not pdf:
        flash("La signature n'est pas encore terminée.", "ok")
        return redirect(url_for("detail", uid=uid))
    store.poser_octets(item["id"], "contrat",
                       store.nom_piece(item, "contrat-signe", ".pdf"), pdf)
    _transition(uid, "ContratSigne", procedure=pid)
    flash("Contrat signé récupéré.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/dpae-faite")
@rh
def dpae_faite(uid):
    item = store.lire(uid) or abort(404)
    if "DpaeFaite" not in store.TRANSITIONS.get(store.etat(item), set()):
        flash("La DPAE ne peut être déclarée qu'une fois le contrat signé.", "erreur")
        return redirect(url_for("detail", uid=uid))
    accuse = request.files.get("accuse")
    if not (accuse and accuse.filename):
        flash("L'accusé DPAE est obligatoire pour déclarer la DPAE faite.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(uid, "contrat", "accuse-dpae", accuse)
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _transition(uid, "DpaeFaite")
    flash("DPAE enregistrée.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/remettre")
@rh
def remettre(uid):
    # Plus de mail par dossier : la remise DUPLIQUE le dossier dans data/compta/
    # et le cabinet recoit un seul mail hebdomadaire (recap.py) avec un lien
    # de lot par dossier remis dans la semaine.
    item = _transition(uid, "RemisComptable")
    if not item:
        return redirect(url_for("detail", uid=uid))
    dest = store.copier_compta(item)
    flash(f"Remis au cabinet comptable — copie dans {dest.relative_to(config.DONNEES)}. "
          "Le lien partira dans le mail hebdomadaire.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/renvoyer")
@rh
def renvoyer(uid):
    store.bump_epoch(uid, "lien_comptable_epoch")             # tue l'ancien lien
    # noter AVANT la copie : nom_export lit la date du dernier renvoi dans le journal.
    store.noter(uid, type="renvoi_comptable", par=session["utilisateur"])
    store.copier_compta(store.lire(uid))
    flash("Copie refaite, l'ancien lien est révoqué ; le dossier repartira dans "
          "le prochain mail hebdomadaire.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/archiver")
@rh
def archiver(uid):
    """Depart du salarie : deplace le dossier vers LEAVERS/ (onglet « Anciens
    salariés »). Terminal, comme abandonner()."""
    date_sortie = (request.form.get("date_sortie") or "").strip()
    if not date_sortie:
        flash("La date de sortie est obligatoire.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.archiver(uid, session["utilisateur"], date_sortie,
                       (request.form.get("motif") or "").strip())
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    flash("Départ enregistré, dossier archivé dans LEAVERS.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/abandonner")
@rh
def abandonner(uid):
    if not _transition(uid, "Abandonnee",
                       motif=(request.form.get("commentaire") or "").strip()):
        return redirect(url_for("detail", uid=uid))
    store.bump_epoch(uid, "lien_comptable_epoch")             # tue le lien du lot
    flash("Dossier abandonné.", "ok")
    return redirect(url_for("detail", uid=uid))


# --- purge (conservation) ----------------------------------------------

def _apercu_purge():
    """Ce qui partirait : une ligne lisible par dossier eligible."""
    lignes = []
    for item, ecoule, duree in store.eligibles():
        prenom, nom = config.identite(item["champs"])
        lignes.append({"id": item["id"],
                       "nom": " ".join(p for p in (prenom, nom.upper()) if p) or item["id"],
                       "etablissement": config.valeur(item["champs"], "etablissement"),
                       "etat": LIBELLES_ETAT.get(store.etat(item), store.etat(item)),
                       "ecoule": ecoule, "duree": duree})
    return lignes


@app.get("/purger")
@rh
def purger_apercu():
    """Aucune action au GET : l'ecran liste ce qui va partir, le POST l'efface."""
    conf = config.conservation()
    return render_template("purger.html", lignes=_apercu_purge(), conf=conf)


@app.post("/purger")
@rh
def purger():
    faits = 0
    for item, _ecoule, _duree in store.eligibles():
        try:
            store.purger(item["id"])          # revérifie l'éligibilité lui-même
            faits += 1
        except OSError as e:
            flash(f"{item['id']} : purge incomplète — {e}", "erreur")
    flash(f"{faits} dossier(s) purgé(s) : pièces effacées, journal conservé."
          if faits else "Aucun dossier à purger.", "ok" if faits else "erreur")
    return redirect(url_for("suivi"))


# --- lot comptable (sans login, possession du lien = acces) --------------

@app.get("/lot/<jeton>")
def lot(jeton):
    item = store.verifier_lien(jeton, "lot_comptable")
    if not item:
        return render_template("message.html", titre="Lien expiré",
                               texte="Ce lien n'est plus valable. Demandez au "
                                     "service RH de vous en renvoyer un."), 410
    store.noter(item["id"], type="acces_lot", par=None, ip=request.remote_addr)
    return render_template("lot.html", item=item, jeton=jeton,
                           pieces=store.fichiers(item, "pieces"),
                           produits=store.fichiers(item, "contrat"),
                           nom_export=store.nom_export)


@app.get("/lot/<jeton>/zip")
def lot_zip(jeton):
    item = store.verifier_lien(jeton, "lot_comptable") or abort(410)
    store.noter(item["id"], type="acces_lot", par=None, ip=request.remote_addr)
    # Le zip entier est assemble en RAM (tampon) avant l'envoi -- c'etait deja
    # le cas quand on streamait depuis le disque. z.writestr ajoute au pic, le
    # temps d'une iteration, un fichier decompresse (<= store.TAILLE_MAX, 15 Mo).
    # simplification volontaire : suffisant a 1-2 utilisateurs et lien par
    # dossier ; passer a un flux (store + z.open) si le lot grossit.
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for bucket in ("pieces", "contrat"):        # jamais _versions
            for nom in store.fichiers(item, bucket):
                z.writestr(f"{store.BUCKETS_COMPTA[bucket]}/{store.nom_export(item, bucket, nom)}",
                           store.ouvrir(item["id"], bucket, nom))
    tampon.seek(0)
    return send_file(tampon, mimetype="application/zip", as_attachment=True,
                     download_name=f"lot-{item['id']}.zip")


@app.get("/lot/<jeton>/fichier/<bucket>/<nom>")
def lot_fichier(jeton, bucket, nom):
    item = store.verifier_lien(jeton, "lot_comptable") or abort(410)
    if bucket not in ("pieces", "contrat"):
        abort(404)
    return _servir(store.ouvrir(item["id"], bucket, nom),
                   store.nom_export(item, bucket, nom), bucket)


# --- verrou multi-machine ----------------------------------------------
# store._verrou n'exclut qu'a l'interieur d'un process : deux serveurs pointes
# sur le meme stockage (trivial avec un dossier synchronise) perdent des
# entrees de journal EN SILENCE. Trois champs et un horodatage previennent
# l'erreur de deploiement -- pas portalocker : on ne cherche pas a arbitrer une
# course, juste a refuser un second demarrage.
_VERROU_TTL = 300          # s : verrou d'une AUTRE machine, non sondable -> perime
                           # au-dela. Sur la meme machine, la mort du pid tranche.


def _fichier_verrou():
    return config.DONNEES / ".serveur-actif.json"


def _ecrire_verrou(moi):
    f = _fichier_verrou()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"hote": moi[0], "pid": moi[1], "le": time.time()}),
                   encoding="utf-8")
    os.replace(tmp, f)


def _pid_vivant(pid):
    """True si le process tourne encore. os.kill(pid, 0) TUE le process sous
    Windows (TerminateProcess) -- y passer par OpenProcess."""
    if not isinstance(pid, int):
        return False
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFO
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # existe, mais pas a nous
    return True


def _verrou_serveur(moi=None):
    """Refuse de demarrer si un AUTRE serveur tient le verrou. « Autre » =
    couple (hote, pid) different ET (meme machine : pid encore vivant ; autre
    machine : verrou de moins de _VERROU_TTL). Un crash sur la meme machine est
    donc repris aussitot ; depuis une autre machine, au bout de 5 min. Puis
    rafraichit le fichier toutes les 60 s (thread demon)."""
    moi = moi or (socket.gethostname(), os.getpid())
    f = _fichier_verrou()
    if f.exists():
        try:
            tenu = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tenu = {}
        autre = (tenu.get("hote"), tenu.get("pid")) != tuple(moi)
        age = time.time() - tenu.get("le", 0)
        meme_machine = tenu.get("hote") == moi[0]
        tient_encore = (_pid_vivant(tenu.get("pid")) if meme_machine
                        else age < _VERROU_TTL)
        if autre and tient_encore:
            raise SystemExit(
                f"Un serveur sert déjà ce stockage : {tenu.get('hote')} "
                f"(pid {tenu.get('pid')}), verrou rafraîchi il y a {int(age)} s. "
                f"Deux serveurs sur le même dossier perdent des entrées de journal "
                f"en silence. Si l'autre a planté, supprimez {f}.")
    _ecrire_verrou(moi)

    def _boucle():
        while True:
            time.sleep(60)
            try:
                _ecrire_verrou(moi)
            except OSError:
                pass
    threading.Thread(target=_boucle, daemon=True).start()


def demarrer():
    """Verifie la config puis sert (waitress, ou Werkzeug si DEBUG)."""
    # Refus dur : installation incomplete (secret/mdp par defaut, aucun
    # etablissement) ou template reclamant un champ inexistant -- tout se
    # decouvre ici, jamais a la generation du contrat d'un vrai salarie.
    if manques := config.verifier():
        for sujet, quoi in manques.items():
            print(f"!! {sujet} : {', '.join(quoi)}")
        raise SystemExit("Configuration incomplète — le serveur ne sert pas. "
                         "Voir ci-dessus ; au besoin : python installer.py \"<Client>\"")
    # « dossier synchronise » seulement si c'est vraiment ce qui sert : DONNEES=
    # sur la ligne de lancement l'emporte sur le bloc stockage, et une ligne de
    # demarrage qui annonce un drive alors qu'on ecrit ailleurs serait pire que
    # pas de mention du tout.
    depuis_config = (config.stockage().get("mode") == "dossier"
                     and config.DONNEES == Path(config.stockage()["chemin"].strip()))
    ou = " (dossier synchronisé)" if depuis_config else ""
    print(f"{config.instance()['client']} — données : {config.DONNEES}{ou}", flush=True)
    # APRES verifier(), jamais avant : `parents=True` creerait lui-meme le chemin
    # du drive, et _verifier_stockage trouverait alors un dossier bien present --
    # l'app servirait en ecrivant des pieces d'identite dans un dossier local qui
    # ressemble a un dossier synchronise. Le garde-fou constate, il ne repare pas.
    for zone in ("soumissions", store.DOSSIERS):
        (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("DEBUG"):
        # Developpement local seulement : serveur Werkzeug + debugger interactif.
        # OFF par defaut, l'app sert des pieces d'identite. Pas de verrou serveur
        # ici : le reloader Werkzeug refork `python app.py`, l'enfant rejouerait
        # demarrer() et buterait sur le verrou du parent -- et un dev local n'a
        # de toute facon pas de stockage partage a proteger.
        app.run(debug=True, port=port)
    else:
        # Apres la creation des zones (memes raisons d'ordre) : refuse un second
        # serveur sur le meme stockage, rafraichit ensuite le verrou en fond.
        _verrou_serveur()
        # Production : waitress. Choisi plutot que gunicorn parce qu'il tourne
        # aussi sous Windows (gunicorn depend de fcntl, absent la) et surtout
        # parce qu'il est MONO-PROCESS multi-thread par construction : la
        # contrainte de store._verrou est alors garantie par le serveur, pas
        # par un drapeau qu'un fichier de service peut ecraser.
        from waitress import serve
        # 127.0.0.1 par defaut, comme app.run() avant lui : l'app se sert
        # DERRIERE un reverse proxy qui porte le TLS, elle ne s'expose pas
        # elle-meme. HOST=0.0.0.0 seulement quand le proxy est ailleurs
        # (autre conteneur, autre machine) -- jamais pour ouvrir sur
        # l'exterieur en clair : le formulaire est public et l'app sert
        # des pieces d'identite.
        hote = os.environ.get("HOST", "127.0.0.1")
        print(f"waitress sur {hote}:{port} (mono-process, 4 threads)", flush=True)
        # waitress efface X-Forwarded-* par defaut AVANT l'app : sans ce
        # drapeau, ProxyFix ne voit rien et PROXIES est sans effet (tous les
        # visiteurs = 127.0.0.1, liens en http://). Garde a 0 : sans proxy
        # declare, l'en-tete reste efface -- il serait fourni par le client.
        serve(app, host=hote, port=port, threads=4,
              clear_untrusted_proxy_headers=not _PROXIES)


if __name__ == "__main__":
    demarrer()
