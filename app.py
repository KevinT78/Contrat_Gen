"""Application : le parcours formulaire -> validation -> dossier -> contrat ->
DPAE -> remise au cabinet comptable.

Aucune action au GET : chaque decision passe par un ecran de confirmation
puis un POST (ticket 08).
"""
import io
import os
import time
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
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
# Trop haut = remote_addr forgeable ; trop bas = tous les visiteurs partagent
# le meme compteur et la Nieme requete legitime est jetee en silence.
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
                 "ContratSigne": "Contrat signé", "RappelDpae": "Rappel DPAE",
                 "DpaeFaite": "DPAE faite", "RemisComptable": "Remis au comptable",
                 "Abandonnee": "Abandonnée"}
TONS_ETAT = {"Soumise": "attente", "ATraiter": "attente", "Rejetee": "ko",
             "ContratPret": "actif", "ContratSigne": "actif",
             "RappelDpae": "actif", "DpaeFaite": "actif",
             "RemisComptable": "", "Abandonnee": ""}


@app.context_processor
def _aides():
    return {"nom_de": _nom,
            # Les ecrans lisaient « item.champs.poste » en dur : chez un client
            # qui renomme ses champs, la colonne sortait VIDE au lieu de crier.
            "valeur": config.valeur,
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
        u = config.instance()["utilisateurs"].get(request.form.get("identifiant", ""))
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
        {c["role"] for c in config.pieces()} - {c["role"] for c in store.manquantes(item)})
    for c in config.champs():
        if c["type"] == "piece_jointe":
            f = request.files.get(c["id"])
            if c.get("requis") and not (f and f.filename) and c["role"] not in deja:
                erreurs.append(f"« {c['libelle'] } » est obligatoire.")
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


@app.route("/", methods=["GET", "POST"])
def formulaire():
    if request.method == "POST":
        if _pot_rempli() or not _soumission_autorisee(request.remote_addr):
            return render_template("message.html", titre="Demande envoyée",
                                   texte="Le service RH a été prévenu. Vous serez "
                                         "recontacté si une pièce manque.")
        champs, erreurs = _saisie()
        if erreurs:
            return render_template("formulaire.html", champs=champs,
                                   erreurs=erreurs, action=url_for("formulaire"))
        uid = store.creer_soumission(champs, request.files)
        ok = _mail(uid, "nouvelle_soumission", config.instance()["mails"]["rh"],
                   nom=_nom(champs), id=uid,
                   lien=url_for("detail", uid=uid, _external=True))
        return render_template("message.html", titre="Demande envoyée",
                               texte=("Le service RH a été prévenu. Vous serez "
                                      "recontacté si une pièce manque." if ok else
                                      "Votre demande est bien enregistrée, mais "
                                      "l'avis au service RH n'a pas pu partir : "
                                      "prévenez-le si vous restez sans réponse."))
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
            return render_template("message.html", titre="Correction envoyée",
                                   texte="Le service RH va réexaminer la demande.")
        champs, erreurs = _saisie(item)
        if erreurs:
            return render_template("formulaire.html", champs=champs, erreurs=erreurs,
                                   ko=ko, action=request.path)
        store.resoumettre(item["id"], champs, request.files)
        ok = _mail(item["id"], "nouvelle_soumission",
                   config.instance()["mails"]["rh"], nom=_nom(champs),
                   id=item["id"],
                   lien=url_for("detail", uid=item["id"], _external=True))
        return render_template("message.html", titre="Correction envoyée",
                               texte=("Le service RH va réexaminer la demande."
                                      if ok else
                                      "Votre correction est bien enregistrée, mais "
                                      "l'avis au service RH n'a pas pu partir : "
                                      "prévenez-le si vous restez sans réponse."))
    return render_template("formulaire.html", champs=item["champs"], erreurs=[],
                           ko=ko, action=request.path)


# --- suivi ---------------------------------------------------------------

@app.get("/suivi")
@rh
def suivi():
    items = store.tout()
    voir_inactifs = request.args.get("inactifs") == "1"
    visibles = [i for i in items
                if voir_inactifs or store.etat(i) not in store.INACTIFS]
    return render_template("suivi.html", items=visibles, etat=store.etat,
                           inactifs=store.INACTIFS, voir_inactifs=voir_inactifs,
                           manquantes=store.manquantes,
                           aujourdhui=date.today().isoformat(),
                           a_traiter=sum(store.etat(i) in ("Soumise", "ATraiter")
                                         for i in items))


@app.get("/dossier/<uid>")
@rh
def detail(uid):
    item = store.lire(uid) or abort(404)
    return render_template("dossier.html", item=item, etat=store.etat(item),
                           pieces=store.fichiers(item, "pieces"),
                           produits=store.fichiers(item, "contrat"),
                           manquantes=store.manquantes(item),
                           motifs=config.instance()["motifs_ko"],
                           champ=config.champ, saisie_rh=config.saisie_rh(),
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


# Le bucket `contrat` peut porter un .html (un client dont les modeles sont en
# HTML, cf. contrat._generer_texte) rempli avec des valeurs venues du formulaire
# public, SANS echappement. Servi inline, ce serait du script sur l'origine de
# l'app -- et /lot/<jeton> est accessible sans compte. Un contrat se telecharge,
# il ne se previsualise pas ; les pieces (pdf/jpg/png) restent en apercu.
def _servir(dossier, nom, bucket):
    return send_from_directory(dossier, nom, as_attachment=(bucket == "contrat"))


@app.get("/dossier/<uid>/fichier/<bucket>/<nom>")
@rh
def fichier(uid, bucket, nom):
    if bucket not in ("pieces", "contrat"):
        abort(404)
    d = store.dossier_de(uid) or abort(404)
    return _servir(d / bucket, nom, bucket)


# --- actions RH ----------------------------------------------------------

def _transition(uid, vers, **extra):
    """store.transition avec le garde de store.TRANSITIONS rendu en flash au
    lieu d'un 500 : un POST hors séquence est refusé proprement."""
    try:
        return store.transition(uid, vers, session["utilisateur"], **extra)
    except ValueError as e:
        flash(str(e), "erreur")
        return None


def _fiche_salarie(item):
    """Génère la fiche salarié dans contrat/ si un template est déclaré.
    Le schéma la place à l'ouverture du dossier. Un échec n'annule pas la
    validation : on note et on continue."""
    modele = config.instance().get("fiche_salarie")
    if not modele:
        return
    present = {f.rsplit(".", 1)[0] for f in store.fichiers(item, "pieces")}
    fournies = [c["libelle"] for c in config.pieces()
                if store.SAIN.sub("-", c["role"]) in present]
    manquantes = [c["libelle"] for c in store.manquantes(item)]
    vals = contrat.valeurs(
        item["champs"],
        config.mentions(config.valeur(item["champs"], "etablissement")),
        extra={"PiecesFournies": ", ".join(fournies) or "—",
               "PiecesManquantes": ", ".join(manquantes) or "aucune"})
    try:
        contrat.generer(config.CLIENT / "contrats" / modele, vals,
                        Path(item["_dir"]) / "contrat" / "fiche-salarie.docx")
        store.noter(item["id"], type="fiche_salarie", par="systeme")
    except (ValueError, OSError) as e:
        store.noter(item["id"], type="fiche_echouee", par="systeme", motif=str(e))
        flash(f"Fiche salarié non générée : {e}", "erreur")


@app.post("/dossier/<uid>/valider")
@rh
def valider(uid):
    try:
        store.valider(uid, session["utilisateur"])
    except ValueError as e:                       # déjà un dossier (double-clic / course)
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _fiche_salarie(store.lire(uid))
    flash("Soumission acceptée : dossier salarié ouvert.", "ok")
    return redirect(url_for("detail", uid=uid))


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
    ok = _mail(uid, "rejet", _email_demandeur(item["champs"]),
               motif=motif, commentaire=commentaire, lien=lien,
               nom=_nom(item["champs"]))
    flash("Demande rejetée, lien de correction envoyé." if ok
          else "Demande rejetée, mais le mail n'est pas parti : le manager n'a "
               "PAS reçu le lien de correction — voir le journal.",
          "ok" if ok else "erreur")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/contrat")
@rh
def generer_contrat(uid):
    item = store.lire(uid) or abort(404)
    if config.mode_contrat(config.valeur(item["champs"], "etablissement")) != "genere":
        flash("Cet établissement est en contrat déposé (myrhis) : "
              "utilisez « Déposer le contrat ».", "erreur")
        return redirect(url_for("detail", uid=uid))
    modele = config.modele_pour(item["champs"])
    if not modele:
        flash(f"Aucun modèle de contrat configuré pour le poste "
              f"« {config.valeur(item['champs'], 'poste')} ».", "erreur")
        return redirect(url_for("detail", uid=uid))
    # Placeholders qu'aucune question du formulaire ne fournit : saisis ici par
    # la RH, fusionnes dans champs pour que la regeneration et le lot les voient.
    extra = {k: (request.form.get(k) or "").strip() for k in config.saisie_rh()}
    if extra:
        item = store.completer_champs(uid, extra)
    vals = contrat.valeurs(item["champs"],
                           config.mentions(config.valeur(item["champs"], "etablissement")),
                           extra=extra)
    dest = Path(item["_dir"]) / "contrat" / ("contrat" + Path(modele).suffix.lower())
    try:
        contrat.generer(config.CLIENT / "contrats" / modele, vals, dest)
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _transition(uid, "ContratPret", modele=modele)
    # Avertissement legal CDD : 2 jours ouvrables avant la date de debut.
    type_c = config.valeur(item["champs"], "type_contrat")
    if type_c == "CDD":
        from datetime import date as _date
        try:
            debut = _date.fromisoformat(config.valeur(item["champs"], "date_debut"))
            reste = _jours_ouvrables_entre(_date.today(), debut)
            if reste < 0:
                flash(f"Attention : la date de début est dépassée de {-reste} "
                      f"jour(s) ouvrable(s).", "erreur")
            elif reste <= 2:
                flash(f"Attention : il reste {reste} jour(s) ouvrable(s) avant "
                      f"la date de début — la remise au salarié doit intervenir "
                      f"dans les 2 jours ouvrables.", "erreur")
        except (ValueError, TypeError):
            pass
    flash("Contrat généré.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/contrat-depose")
@rh
def contrat_depose(uid):
    """Couloir Restaurant : le contrat est fait à la main sur myrhis, la RH
    dépose le PDF/.docx ici."""
    item = store.lire(uid) or abort(404)
    d = store.dossier_de(uid) or abort(404)
    if config.mode_contrat(config.valeur(item["champs"], "etablissement")) != "depose":
        flash("Cet établissement génère son contrat : utilisez « Générer ».", "erreur")
        return redirect(url_for("detail", uid=uid))
    f = request.files.get("contrat")
    if not (f and f.filename):
        flash("Le fichier du contrat est obligatoire.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(d, "contrat", "contrat", f, extensions=CONTRAT_EXT)
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
    d = store.dossier_de(uid) or abort(404)
    f = request.files.get("signe")
    if not (f and f.filename):
        flash("Le contrat signé est obligatoire.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(d, "contrat", "contrat-signe", f, extensions=CONTRAT_EXT)
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
    src = next((c for c in contrats if c.startswith("contrat.")), None)
    if not src:
        flash("Aucun contrat à envoyer.", "erreur")
        return redirect(url_for("detail", uid=uid))
    c = item["champs"]
    try:
        pid = signature.envoyer(
            Path(item["_dir"]) / "contrat" / src,
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
    (Path(item["_dir"]) / "contrat" / "contrat-signe.pdf").write_bytes(pdf)
    _transition(uid, "ContratSigne", procedure=pid)
    flash("Contrat signé récupéré.", "ok")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/rappel-dpae")
@rh
def rappel_dpae(uid):
    item = _transition(uid, "RappelDpae")
    if not item:
        return redirect(url_for("detail", uid=uid))
    conf = config.instance()["mails"]
    ok = _mail(uid, "rappel_dpae", conf.get("dpae") or conf["rh"],
               nom=_nom(item["champs"]),
               debut=config.valeur(item["champs"], "date_debut"),
               lien=url_for("detail", uid=uid, _external=True))
    flash("Rappel DPAE envoyé." if ok
          else "Rappel DPAE NON envoyé — voir le journal.", "ok" if ok else "erreur")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/dpae-faite")
@rh
def dpae_faite(uid):
    item = store.lire(uid) or abort(404)
    d = Path(item["_dir"])
    if "DpaeFaite" not in store.TRANSITIONS.get(store.etat(item), set()):
        flash("La DPAE ne peut être déclarée qu'une fois le contrat signé.", "erreur")
        return redirect(url_for("detail", uid=uid))
    accuse = request.files.get("accuse")
    if not (accuse and accuse.filename):
        flash("L'accusé DPAE est obligatoire pour déclarer la DPAE faite.", "erreur")
        return redirect(url_for("detail", uid=uid))
    try:
        store.deposer(d, "contrat", "accuse-dpae", accuse)
    except ValueError as e:
        flash(str(e), "erreur")
        return redirect(url_for("detail", uid=uid))
    _transition(uid, "DpaeFaite")
    flash("DPAE enregistrée.", "ok")
    return redirect(url_for("detail", uid=uid))


def _avis_comptable(item, epoch):
    lien = url_for("lot", jeton=store.signer("lot_comptable", item["id"], epoch),
                   _external=True)
    ok = _mail(item["id"], "avis_comptable",
               config.comptable(config.valeur(item["champs"], "etablissement")),
               nom=_nom(item["champs"]), id=item["id"], lien=lien)
    return ok, lien


@app.post("/dossier/<uid>/remettre")
@rh
def remettre(uid):
    item = _transition(uid, "RemisComptable")
    if not item:
        return redirect(url_for("detail", uid=uid))
    ok, _ = _avis_comptable(item, item.get("lien_comptable_epoch", 0))
    flash("Remis au cabinet comptable." if ok
          else "Dossier remis, mais l'avis n'est pas parti — voir le journal.",
          "ok" if ok else "erreur")
    return redirect(url_for("detail", uid=uid))


@app.post("/dossier/<uid>/renvoyer")
@rh
def renvoyer(uid):
    epoch = store.bump_epoch(uid, "lien_comptable_epoch")     # tue l'ancien lien
    item = store.lire(uid)
    _avis_comptable(item, epoch)
    store.noter(uid, type="renvoi_comptable", par=session["utilisateur"])
    flash("Nouvel avis envoyé, l'ancien lien est révoqué.", "ok")
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
                           produits=store.fichiers(item, "contrat"))


@app.get("/lot/<jeton>/zip")
def lot_zip(jeton):
    item = store.verifier_lien(jeton, "lot_comptable") or abort(410)
    store.noter(item["id"], type="acces_lot", par=None, ip=request.remote_addr)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for bucket in ("pieces", "contrat"):        # jamais _versions
            for nom in store.fichiers(item, bucket):
                z.write(Path(item["_dir"]) / bucket / nom, f"{bucket}/{nom}")
    tampon.seek(0)
    return send_file(tampon, mimetype="application/zip", as_attachment=True,
                     download_name=f"lot-{item['id']}.zip")


@app.get("/lot/<jeton>/fichier/<bucket>/<nom>")
def lot_fichier(jeton, bucket, nom):
    item = store.verifier_lien(jeton, "lot_comptable") or abort(410)
    if bucket not in ("pieces", "contrat"):
        abort(404)
    return _servir(Path(item["_dir"]) / bucket, nom, bucket)


if __name__ == "__main__":
    for zone in ("soumissions", "documents"):
        (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)
    # Refus dur : installation incomplete (secret/mdp par defaut, aucun
    # etablissement) ou template reclamant un champ inexistant -- tout se
    # decouvre ici, jamais a la generation du contrat d'un vrai salarie.
    if manques := config.verifier():
        for sujet, quoi in manques.items():
            print(f"!! {sujet} : {', '.join(quoi)}")
        raise SystemExit("Configuration incomplète — le serveur ne sert pas. "
                         "Voir ci-dessus ; au besoin : python installer.py \"<Client>\"")
    print(f"{config.instance()['client']} — données : {config.DONNEES}", flush=True)
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("DEBUG"):
        # Developpement local seulement : serveur Werkzeug + debugger interactif.
        # OFF par defaut, l'app sert des pieces d'identite.
        app.run(debug=True, port=port)
    else:
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
        serve(app, host=hote, port=port, threads=4)
