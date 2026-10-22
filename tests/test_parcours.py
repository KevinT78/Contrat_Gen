"""Le parcours complet, les deux couloirs du schéma, de bout en bout.

    python tests/test_parcours.py

Écrit dans une base vide (mongomock par défaut) et un dossier temporaire
(mails), rien sur le disque.
  - couloir Dark Kitchen : Lille Grand Place, contrat généré
  - couloir Restaurant   : Marseille Prado, contrat déposé
Contrat signé non bloquant, déposé après la remise sans changer d'état. Fiche salarié générée dans contrat/ dès la
validation. recap.py liste le salarié de la semaine. Plus les refus attendus.
"""
import copy
import email
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo  # noqa: E402
base_mongo("parcours")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402
from _outils import compte, corps_texte, mails  # noqa: E402
compte("rh", "fixture")


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF", "p.pdf")


def piece():
    return (BytesIO(PDF[0]), PDF[1])


def base_saisie(etab, poste="Manager"):
    return {
        "etablissement": etab,
        "email_demandeur": "manager@example.com",
        "nom_naissance": "Martin", "nom_usage": "", "prenom": "Camille",
        "date_naissance": "1998-04-11", "lieu_naissance": "Lille",
        "nationalite": "Française", "num_secu": "2 98 04 59 350 042 21",
        "adresse": "14 rue Nationale, 59000 Lille",
        "poste": poste, "type_contrat": "CDI", "date_debut": "2026-10-01",
        "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450",
    }


def dernier_mail(nom):
    envoyes = mails(nom)
    assert envoyes, f"aucun mail « {nom} » envoye"
    msg = envoyes[-1]
    return msg["To"] + "\n" + corps_texte(msg)


def lien_dans(mail, chemin):
    m = re.search(rf"(http://localhost/{chemin}/[\w.=-]+)", mail)
    assert m, f"pas de lien /{chemin} dans le mail :\n{mail}"
    return m.group(1)


def soumettre(c, saisie):
    r = c.post("/demande", data={**saisie, "identite": [piece(), piece()],
                          "carte_vitale": piece(), "rib": piece()},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())      # ULID le plus récent


def piece_refusee(_c):
    """Formulaire public : une pièce hors liste blanche (.docx) est un message,
    pas un 500, et ne laisse aucune soumission à moitié née sur le disque.
    Client dédié : l'erreur met les bonnes pièces de côté (brouillon), rien ne
    doit fuiter dans la session d'un appelant qui réutilise son client."""
    c = app.test_client()
    avant = (store.db().dossiers.count_documents({}), store.db().fichiers.count_documents({}))
    r = c.post("/demande", data={**base_saisie("ACME Restauration / Lille Grand Place"),
                          "identite": piece(), "carte_vitale": piece(),
                          "rib": (BytesIO(b"PK"), "rib.docx")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "format .docx refusé" in r.text, \
        (r.status_code, r.text[:300])
    apres = (store.db().dossiers.count_documents({}), store.db().fichiers.count_documents({}))
    assert apres == avant, f"soumission ou pièces orphelines créées : {avant} -> {apres}"


def couloir_dark_kitchen(c):
    """Lille Grand Place — contrat généré par l'app, signature manuelle."""
    piece_refusee(c)
    uid = soumettre(c, base_saisie("ACME Restauration / Lille Grand Place"))

    # KO puis correction
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante", "commentaire": "RIB flou"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    ko = dernier_mail("rejet")
    assert ko.startswith("rh@example.com\n"), \
        "établissement sans manager_email : le rejet doit retomber sur mails.rh, " \
        "jamais sur l'email saisi (celui du candidat)"
    lien = lien_dans(ko, "corriger")
    r = c.post(lien, data={**base_saisie("ACME Restauration / Lille Grand Place"),
                           "rib": piece()}, content_type="multipart/form-data")
    assert "Correction envoyée" in r.text
    assert c.get(lien).status_code == 410, "lien de correction encore vivant"
    assert store.fichiers(store.lire(uid), "_versions"), "re-dépôt non archivé"

    # validation -> dossier + fiche salarié + contrat généré dans la foulée
    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert item["zone"] == "documents" and store.etat(item) == "ContratPret"
    assert dernier_mail("demande_validee").startswith("rh@example.com\n"), \
        "établissement sans manager_email : l'avis de validation doit aller à mails.rh"
    assert store._hierarchie(item) == ("DARK KITCHENS", "Lille Grand Place", "Manager",
                                       "MARTIN Camille"), store._hierarchie(item)
    # dossier validé : noms lisibles « <Libellé> - NOM Prénom.ext »
    assert store.fichiers_role(item, "pieces", "fiche-salarie") == \
        ["Fiche salarié - MARTIN Camille.docx"], store.fichiers(item, "pieces")
    assert any(e.get("type") == "fiche_salarie" for e in item["journal"])
    # le rappel DPAE est un EFFET de la validation (schéma, étape 6) : asserté
    # ici, au niveau de l'appelant, avec les informations DPAE et le SIRET
    rappel = dernier_mail("rappel_dpae")
    for attendu in ("Martin", "01/10/2026", "884 512 336 00027",
                    "Lille Grand Place", "http://localhost/dossier/"):
        assert attendu in rappel, f"« {attendu} » absent du rappel DPAE :\n{rappel}"

    # refus : RemisComptable direct (garde de store.TRANSITIONS)
    try:
        store.transition(uid, "RemisComptable", "test")
        assert False, "transition ContratPret -> RemisComptable acceptée"
    except ValueError:
        pass

    # contrat produit à la validation, sans étape « Générer » séparée
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert item["journal"][-1]["modele"] == "CDI_Manager.docx"
    from docx import Document
    import contrat as moteur
    nom_ct = store.fichiers_role(item, "contrat", "contrat")[0]
    assert nom_ct == "Contrat - MARTIN Camille.docx", nom_ct
    doc = Document(BytesIO(store.ouvrir(item["id"], "contrat", nom_ct)))
    texte = "\n".join(p.text for p in moteur.paragraphes(doc))
    assert "{{" not in texte, "contrat troué"
    for attendu in ("Camille MARTIN", "1er octobre 2026", "2450",
                    "ACME RESTAURATION SAS", "884 512 336 00027", "Lille Grand Place"):
        assert attendu in texte, f"« {attendu} » absent du contrat"

    # DPAE faite (accusé obligatoire)
    c.post(f"/dossier/{uid}/dpae-faite", data={}, content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret", "DPAE validée sans accusé"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"

    # remise : plus de mail par dossier, lien dans le mail hebdo. Le lot porte
    # contrat, fiche salarié, accusé, chacun renommé « <libellé> - Nom - date ».
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    assert not mails("avis_comptable"), \
        "un mail par dossier est encore parti à la remise"
    attendu = sorted(f"{store.BUCKETS[b]}/{store.nom_export(item, b, n)}"
                     for b in ("pieces", "contrat") for n in store.fichiers(item, b))
    lot = "http://localhost/lot/" + store.signer("lot_comptable", uid,
                                                 item.get("lien_comptable_epoch", 0))
    anonyme = app.test_client()
    presents = sorted(ZipFile(BytesIO(anonyme.get(lot + "/zip").data)).namelist())
    assert presents == attendu, (presents, attendu)
    assert all(re.search(r" - MARTIN Camille - \d{4}-\d{2}-\d{2}\.\w+$", n)
               for n in presents), presents
    # jour LOCAL figé à la remise (entre 0 h et 2 h à Paris, l'UTC dit la veille)
    remise = next(e for e in reversed(item["journal"]) if store.est_remise(e))
    assert remise.get("jour") == date.today().isoformat(), remise
    assert all(f" - {date.today().isoformat()}." in n for n in presents), presents
    assert {"FICHE PERSONNELLE", "CONTRAT"} == {n.split("/")[0] for n in presents}

    # Le lot est public (possession du lien = accès) et un contrat peut être un
    # .html rempli de valeurs venues du formulaire public, sans échappement :
    # servi inline ce serait du script sur l'origine de l'app.
    from urllib.parse import quote
    nom_ct = store.fichiers_role(item, "contrat", "contrat")[0]
    nom_id = store.fichiers_role(item, "pieces", "identite")[0]
    r = anonyme.get(f"{lot}/fichier/contrat/{quote(nom_ct)}")
    assert "attachment" in r.headers.get("Content-Disposition", ""), r.headers
    assert r.headers.get("X-Content-Type-Options") == "nosniff", r.headers
    apercu = anonyme.get(f"{lot}/fichier/pieces/{quote(nom_id)}")
    assert "attachment" not in apercu.headers.get("Content-Disposition", ""), \
        "les pièces restent en aperçu"

    # renvoi : l'ancien lien meurt
    c.post(f"/dossier/{uid}/renvoyer")
    assert anonyme.get(lot).status_code == 410
    return uid


def couloir_restaurant(c):
    """Marseille Prado — contrat fait dans le logiciel RH puis déposé."""
    uid = soumettre(c, base_saisie("ACME Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ATraiter"

    # refus : « Générer le contrat » sur un établissement en mode déposé
    r = c.post(f"/dossier/{uid}/contrat", follow_redirects=True)
    assert store.etat(store.lire(uid)) == "ATraiter", "contrat généré sur un établissement déposé"
    assert "contrat déposé" in r.text or "logiciel RH" in r.text

    # dépôt du contrat -> ContratPret
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert store.fichiers_role(item, "contrat", "contrat") == \
        ["Contrat - MARTIN Camille.pdf"], store.fichiers(item, "contrat")

    # DPAE directe (sans rappel)
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    return uid


def dpae_sans_signature(c):
    """DPAE directe depuis ContratPret, contrat signé non bloquant (déposé
    après, sans changer d'état), remise sans lui."""
    uid = soumettre(c, base_saisie("ACME Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    # refus : pas de contrat signé avant la remise
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert not store.fichiers_role(store.lire(uid), "contrat", "contrat-signe")
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite", "DPAE refusée sans signature"
    # POST rejoué (second onglet) : la vraie raison, pas « contrat prêt »
    n = len(store.lire(uid)["journal"])
    r = c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
               content_type="multipart/form-data", follow_redirects=True)
    assert "La DPAE est déjà enregistrée." in r.text, "DPAE rejouée : mauvais message"
    assert len(store.lire(uid)["journal"]) == n
    # refus : le signé ne se dépose qu'une fois remis (vue Salarié)
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert not store.fichiers_role(store.lire(uid), "contrat", "contrat-signe"), \
        "signé accepté en DPAE faite"
    c.post(f"/dossier/{uid}/remettre")
    assert store.etat(store.lire(uid)) == "RemisComptable"
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable", "le dépôt du contrat signé a changé l'état"
    assert store.fichiers_role(item, "contrat", "contrat-signe"), store.fichiers(item, "contrat")
    assert item["journal"][-1]["type"] == "contrat_signe", item["journal"][-1]
    # un second reçu est refusé : ni écrasement, ni seconde entrée de journal
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    apres = store.lire(uid)
    assert len(apres["journal"]) == len(item["journal"]), apres["journal"][-1]
    assert store.fichiers(apres, "contrat") == store.fichiers(item, "contrat")

    # remise sans aucun contrat signé
    uid = soumettre(c, base_saisie("ACME Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable", item["journal"][-1]
    assert not store.fichiers_role(item, "contrat", "contrat-signe")


def export_zip(c, dk, resto, apres_depart=False):
    """Export RH : arborescence recalculée, LEAVERS/ après le départ, purgé
    absent, jamais `_versions`, accents lisibles, mêmes filtres que /salaries."""
    def noms(url):
        r = c.get(url)
        assert r.status_code == 200 and r.mimetype == "application/zip", (url, r.status_code)
        assert "no-store" in r.headers["Cache-Control"], "archive de pièces d'identité cachable"
        return ZipFile(BytesIO(r.data)).namelist()

    racine = "LEAVERS" if apres_depart else "DOSSIERS SALARIES"
    avant_j = sum(e.get("type") == "export" for e in store.lire(dk)["journal"])
    z = noms(f"/dossier/{dk}/export")
    trace = store.lire(dk)["journal"][-1]
    assert trace.get("type") == "export" and trace["par"] == "rh" and trace.get("ip"), trace
    assert sum(e.get("type") == "export" for e in store.lire(dk)["journal"]) == avant_j + 1
    assert c.get("/dossier/inconnu/export").status_code == 404
    assert sum(e.get("type") == "export" for e in store.lire(dk)["journal"]) == avant_j + 1, \
        "trace écrite sans export"
    assert z and all(n.startswith(f"{racine}/DARK KITCHENS/Lille Grand Place/Manager/"
                                  "MARTIN Camille/") for n in z), z
    assert any("/FICHE PERSONNELLE/Fiche salarié - MARTIN Camille.docx" in n for n in z), z
    assert any("/CONTRAT/" in n for n in z), z
    assert {n.split("/")[-2] for n in z} == {"FICHE PERSONNELLE", "CONTRAT"}, \
        "anciennes versions exportées"
    assert app.test_client().get(f"/dossier/{dk}/export").status_code == 302, "export sans login"
    # un <img> tiers ne declenche pas l'export (ni sa trace) : cookie non envoye
    assert app.config["SESSION_COOKIE_SAMESITE"] in ("Lax", "Strict")

    onglet = "/salaries/export?anciens=1" if apres_depart else "/salaries/export"
    lot = noms(onglet)
    assert set(z) <= set(lot), "le dossier manque à l'export de son onglet"
    en_poste = noms("/salaries/export")
    assert not any(n.startswith("LEAVERS/") for n in en_poste), "un ancien parmi les salariés en poste"
    assert (set(z) <= set(en_poste)) == (not apres_depart), "onglet en poste"
    assert not apres_depart or not set(z) & set(en_poste), "un ancien parmi les salariés en poste"
    # deux salariés de même nom au même poste restent deux dossiers distincts
    avec_pieces = [i for i in store.tout() if store.etat(i) == "RemisComptable"
                   and not i.get("purge") and store.fichiers(i, "pieces")]
    assert len(avec_pieces) >= 3, "le jeu d'essai n'a plus d'homonymes"
    assert len({"/".join(n.split("/")[:5]) for n in en_poste}) == len(avec_pieces), \
        "deux homonymes fusionnés dans le même dossier du zip"
    # mêmes filtres que /salaries : un autre établissement ne livre pas Martin
    filtre = noms(onglet + ("&" if "?" in onglet else "?")
                  + "etablissement=ACME Sud / Marseille Prado")
    assert not any("DARK KITCHENS" in n for n in filtre), filtre
    if not apres_depart:
        assert any("/Marseille Prado/" in n for n in filtre), "le filtre vide l'export"
        assert noms(onglet + "?q=zzzintrouvable") == [], "la recherche est ignorée"
        brut = c.get(f"/dossier/{dk}/export").data
        zdk = ZipFile(BytesIO(brut))
        assert zdk.testzip() is None and len(zdk.namelist()) == len(set(zdk.namelist()))
        base = [store.ouvrir(dk, b, f) for b in store.BUCKETS
                for f in store.fichiers(store.lire(dk), b)]
        assert len(zdk.namelist()) == len(base) and \
            Counter(zdk.read(n) for n in zdk.namelist()) == Counter(base), \
            "octets exportés différents de ceux de la base"
        export_sans_piece_ni_trace(c, dk)

    # purgé : plus rien à exporter, ni par dossier ni dans le lot
    avant = noms(onglet)
    store.db().dossiers.update_one({"_id": dk}, {"$set": {"purge": {"le": "x", "jours": 1}}})
    try:
        tracees = sum(e.get("type") == "export" for e in store.lire(dk)["journal"])
        apres = noms(onglet)
        assert sum(e.get("type") == "export" for e in store.lire(dk)["journal"]) == tracees, \
            "export tracé pour un dossier purgé, dont rien n'est sorti"
        assert len(apres) < len(avant), "dossier purgé exporté (ou rien retiré)"
        assert c.get(f"/dossier/{dk}/export").status_code == 404, "dossier purgé exporté seul"
    finally:
        store.db().dossiers.update_one({"_id": dk}, {"$unset": {"purge": ""}})


def export_sans_piece_ni_trace(c, dk):
    """Un dossier sans pièce : zip vide, rien au journal (rien n'est sorti). Et
    une note technique (mail raté, export) ne remet pas « dernière activité »
    de /suivi à aujourd'hui."""
    vide = store.creer_soumission(dict(store.lire(dk)["champs"]), {})
    il_y_a_10_j = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    store.db().dossiers.update_one({"_id": vide}, {"$set": {"journal.0.le": il_y_a_10_j}})
    try:
        r = c.get(f"/dossier/{vide}/export")
        assert r.status_code == 200 and ZipFile(BytesIO(r.data)).namelist() == [], r.status_code
        assert not any(e.get("type") == "export" for e in store.lire(vide)["journal"]), \
            "export tracé pour un dossier dont rien n'est sorti"
        store.noter(vide, type="mail_echoue", par="systeme")
        store.noter(vide, type="export", par="rh")
        assert store.derniere_activite(store.lire(vide)) == il_y_a_10_j
        page = c.get("/suivi").get_data(as_text=True)
        ligne = page.split(f'/dossier/{vide}"', 1)[1].split("</tr>", 1)[0]
        assert "il y a 10 j" in ligne, "dernière activité remise à zéro par une note technique"
    finally:
        store.db().dossiers.delete_one({"_id": vide})


def cas_noms_du_zip():
    """Noms du zip sous Windows : homonymes à la casse près, caractères
    illégaux, noms réservés, noms longs, doublons après nettoyage ; ordre stable."""
    modele = next(i for i in store.tout() if store.etat(i) == "RemisComptable")

    def faux(uid, prenom, pieces):
        return {**modele, "id": uid, "journal": modele["journal"],
                "champs": {**modele["champs"], "prenom": prenom, "nom_naissance": "Dupont"},
                "_fichiers": {"pieces": pieces}}

    # formulaire à champ unique « NOM Prénom » : identite() rend ("", "Équipier
    # Camille") ; le prénom ne doit pas passer en majuscules
    identite, champs = config.identite, config.champs
    config.identite = lambda ch: ("", "Équipier Camille")
    config.champs = lambda: [c for c in champs() if c["id"] != config.role("prenom")]
    try:
        assert store._hierarchie(modele)[3] == "Équipier Camille", store._hierarchie(modele)
        assert store._qui(modele) == "Équipier Camille", store._qui(modele)
    finally:
        config.identite, config.champs = identite, champs
    # champs séparés, prénom facultatif laissé vide : le nom reste en majuscules,
    # partout où un nom est composé (export, aperçu de purge, mail hebdo)
    sans_prenom = faux("01AAAA0003", "", [])
    assert store._qui(sans_prenom) == "DUPONT", store._qui(sans_prenom)
    import recap
    assert recap.resume(sans_prenom).startswith("DUPONT — "), recap.resume(sans_prenom)
    assert store.nom_complet(modele["champs"], prenom_devant=True) == "Camille MARTIN"

    # segments : point de fin après troncature, nom réservé
    assert store._segment("x" * 38 + ". y", "D") == "x" * 38, store._segment("x" * 38 + ". y", "D")
    assert store._segment("nul", "D") == "_nul"
    # LEAVERS pour un Parti, purgé ignoré
    parti = {**faux("01AAAA0005", "Léa", ["ID.pdf"]),
             "journal": modele["journal"] + [{**modele["journal"][-1], "vers": "Parti"}]}
    purge = {**faux("01AAAA0006", "Zoé", ["ID.pdf"]), "purge": {"le": "x", "jours": 1}}
    entrees = store._entrees_zip([parti, purge])
    assert [e[1] for e in entrees] == ["01AAAA0005"], entrees
    assert entrees[0][0].startswith("LEAVERS/"), entrees
    # chemin complet sous CHEMIN_MAX, même au pire (homonyme, doublon après troncature)
    geant = {**modele, "id": "01AAAA0007", "journal": modele["journal"],
             "champs": {**modele["champs"], "etablissement": "E" * 80, "poste": "P" * 80,
                        "nom_naissance": "N" * 80, "prenom": "Q" * 80},
             "_fichiers": {"pieces": ["y" * 300 + "1.pdf", "y" * 300 + "2.pdf"]}}
    double = {**geant, "id": "01AAAA0008"}
    longs = [e[0] for e in store._entrees_zip([geant, double])]
    assert len(longs) == 4 and len(set(longs)) == 4, longs
    assert all(len(c) <= store.CHEMIN_MAX and c.endswith(".pdf") for c in longs), \
        [len(c) for c in longs]

    longue = "x" * 300 + ".pdf"
    a = faux("01AAAA0001", "Camille", ["a:b.pdf", "a-b.pdf", "CON.pdf", longue, "fin. "])
    b = faux("01AAAA0002", "camille", ["ID.pdf"])
    chemins = [e[0] for e in store._entrees_zip([b, a])]
    assert chemins == [e[0] for e in store._entrees_zip([a, b])], "ordre d'arrivée"
    assert len({c.casefold() for c in chemins}) == len(chemins), chemins
    assert not any(":" in c for c in chemins), chemins
    assert any(c.endswith("/_CON.pdf") for c in chemins), chemins
    assert all(len(c.rsplit("/", 1)[1]) <= 100 for c in chemins), chemins
    assert any(c.endswith(".pdf") and len(c.rsplit("/", 1)[1]) > 90 for c in chemins), chemins
    dossiers = {c.rsplit("/", 2)[0] for c in chemins}
    assert len({d.casefold() for d in dossiers}) == 2, dossiers
    assert any(d.endswith("DUPONT camille (0002)") for d in dossiers), dossiers
    assert not any(c.endswith(".") or c.endswith(" ") for c in chemins), chemins


def depart_leaver(c, uid):
    """Départ d'un salarié remis au comptable : état Parti, pièces gardées,
    visible seulement dans /salaries?anciens=1, écran détail sans 500, et un
    second archivage refusé proprement une fois Parti (pas de 500)."""
    pieces = store.fichiers(store.lire(uid), "pieces")

    c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "31/02/2026"})
    assert store.etat(store.lire(uid)) == "RemisComptable", "date de sortie impossible acceptée"
    c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "30/09/2026"})
    item = store.lire(uid)
    assert store.etat(item) == "Parti", item["journal"][-1]
    assert store.date_sortie(item) == "2026-09-30", "date de sortie saisie jj/mm/aaaa mal stockée"
    assert store.fichiers(item, "pieces") == pieces, "pièces perdues à l'archivage"

    assert c.get("/suivi").text.find(uid) == -1, "encore visible dans /suivi"
    assert uid not in c.get("/salaries").text, "encore dans les salariés en poste"
    assert uid in c.get("/salaries?anciens=1").text, "absent de /salaries?anciens=1"

    r = c.get(f"/dossier/{uid}")
    assert r.status_code == 200, "500 sur l'écran détail (garde flux.index)"

    r = c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "2026-10-01"},
               follow_redirects=True)
    assert r.status_code == 200, "second archivage : 500 au lieu d'un refus propre"
    journal = store.lire(uid)["journal"]
    assert journal[-1]["vers"] == "Parti"
    assert sum(e["vers"] == "Parti" for e in journal) == 1, journal


def cas_double_dpae():
    """Deux écrivains sur le même dossier : le second a lu l'état AVANT
    l'écriture du premier. Le compare-and-swap de store._journaliser le force à
    relire : une seule entrée DpaeFaite, et le second lève « transition
    interdite » au lieu d'écrire par-dessus."""
    champs = {config.role("nom_naissance"): "Martin", config.role("prenom"): "Camille",
              config.role("etablissement"): "ACME Restauration / Lille Grand Place",
              config.role("poste"): "Manager"}
    uid = store.creer_soumission(champs, {})
    store.valider(uid, "test")
    store.transition(uid, "ContratPret", "test")
    perime = store.lire(uid)
    store.transition(uid, "DpaeFaite", "test")
    vrai, servi = store.lire, []

    def lire_perime(u):
        if not servi:
            servi.append(u)
            return copy.deepcopy(perime)
        return vrai(u)

    store.lire = lire_perime
    try:
        store.transition(uid, "DpaeFaite", "test")
        leve = None
    except ValueError as e:
        leve = e
    finally:
        store.lire = vrai
    journal = store.lire(uid)["journal"]
    assert servi, "l'item périmé n'a pas été servi : le test ne prouve rien"
    assert sum(e["vers"] == "DpaeFaite" for e in journal) == 1, journal
    assert leve and "transition interdite" in str(leve), leve


def cas_course_champs():
    """Deux completer() au même état : le second a lu AVANT le premier. Le $set
    champ par champ ne perd aucun des deux (un $set du dict entier perdait `a`)."""
    uid = store.importer({"a": "", "b": ""}, "course-champs", {"par": "test"}, [])
    perime = store.lire(uid)
    store.completer(uid, {"a": "1"}, "test", "x")
    vrai, servi = store.lire, []

    def lire_perime(u):
        if not servi:
            servi.append(u)
            return copy.deepcopy(perime)
        return vrai(u)

    store.lire = lire_perime
    try:
        store.completer(uid, {"b": "2"}, "test", "x")
    finally:
        store.lire = vrai
    ch = store.lire(uid)["champs"]
    assert servi, "l'item périmé n'a pas été servi : le test ne prouve rien"
    assert ch["a"] == "1" and ch["b"] == "2", ch


def cas_dossier_inconnu_et_valider_apres_rejet():
    """Uid inconnu : ValueError comme _journaliser, pas un TypeError sur None.
    Valider une soumission rejetée : le journal dit d'où elle part vraiment."""
    for appel in (lambda: store.bump_epoch("inconnu", "lien_comptable_epoch"),
                  lambda: store.completer_champs("inconnu", {"a": "1"})):
        try:
            appel()
            raise AssertionError("uid inconnu accepté")
        except ValueError as e:
            assert "dossier inconnu" in str(e), e
    champs = {config.role("nom_naissance"): "Durand", config.role("prenom"): "Léa",
              config.role("etablissement"): "ACME Restauration / Lille Grand Place",
              config.role("poste"): "Manager"}
    uid = store.creer_soumission(champs, {})
    store.rejeter(uid, "Autre", "", "test")
    store.valider(uid, "test")
    dernier = store.lire(uid)["journal"][-1]
    assert (dernier["de"], dernier["vers"]) == ("Rejetee", "ATraiter"), dernier


def fiche_generique_sans_cle(c):
    """Sans "fiche_salarie" dans instance.json, la fiche générique (modèle
    versé au dépôt, hors config/) est quand même produite."""
    garde = config.instance().pop("fiche_salarie", None)
    try:
        uid = soumettre(c, base_saisie("ACME Restauration / Paris Opéra"))
        c.post(f"/dossier/{uid}/valider")
        item = store.lire(uid)
        noms = store.fichiers_role(item, "pieces", "fiche-salarie")
        assert noms, "fiche générique non produite"
        from docx import Document
        import contrat as moteur
        doc = Document(BytesIO(store.ouvrir(uid, "pieces", noms[0])))
        texte = "\n".join(p.text for p in moteur.paragraphes(doc))
        assert "{{" not in texte, "fiche générique trouée"
        # NumeroSecu / Adresse : synonymes de NumSS / Domicile dans le modele
        for attendu in ("Camille MARTIN", "Paris Opéra", "884 512 336 00019",
                        "2 98 04 59 350 042 21", "14 rue Nationale"):
            assert attendu in texte, f"« {attendu} » absent de la fiche générique"
        # sections sans aucune donnee dans cette config : retirees, titre compris
        for absent in ("Titre de séjour", "Disponibilités", "Heure de démarrage"):
            assert absent not in texte, f"« {absent} » aurait dû être retiré"
        assert "État civil" in texte and "Pièces" in texte, texte
    finally:
        if garde:
            config.instance()["fiche_salarie"] = garde


def contrat_peut_citer_les_pieces_jointes(c):
    """`{{PiecesFournies}}` / `{{PiecesManquantes}}` sont annoncés au client par
    PLACEHOLDERS.md, donc il les met dans son contrat.

    Trois garde-fous disaient OK et le contrat sortait quand même refusé devant
    la RH : le démarrage (la source est déclarée), `doctor` (qui INJECTE les
    deux valeurs lui-même), et la fiche salarié (qui les fournit). Seul
    `_produire_contrat` ne les passait pas -- le chemin du vrai contrat."""
    modele = config.CLIENT / "contrats" / "_pieces_jointes.html"
    modele.write_text("<p>{{Prenom}} {{NomNaissance}} — {{Poste}}</p>"
                      "<p>Pièces fournies : {{PiecesFournies}}</p>"
                      "<p>Pièces manquantes : {{PiecesManquantes}}</p>",
                      encoding="utf-8")
    garde = config.instance()["templates"]
    try:
        config.instance()["templates"] = {**garde, "Manager": "_pieces_jointes.html"}
        uid = soumettre(c, base_saisie("ACME Restauration / Paris Opéra"))
        r = c.post(f"/dossier/{uid}/valider", follow_redirects=True)
        item = store.lire(uid)
        assert store.etat(item) == "ContratPret", \
            "contrat non généré — " + " | ".join(
                re.findall(r"[Cc]ontrat[^<]*génér[^<]*", r.text) or ["(aucun message)"])
        noms = store.fichiers_role(item, "contrat", "contrat")
        assert noms, "aucun contrat déposé dans le dossier"
        from docx import Document
        import contrat as moteur
        texte = "\n".join(p.text for p in moteur.paragraphes(
            Document(BytesIO(store.ouvrir(uid, "contrat", noms[0])))))
        assert "{{" not in texte, f"contrat troué : {texte}"
        assert "Pièces fournies :" in texte, texte
    finally:
        config.instance()["templates"] = garde
        modele.unlink(missing_ok=True)


def valider_survit_a_un_etablissement_retire(c):
    """Un établissement fermé, retiré des sociétés, alors qu'un vieux
    dossier attend encore : la validation ne doit pas rendre un 500.

    config.mentions() lève KeyError sur une clé inconnue. L'appel était HORS du
    try de _fiche_salarie, et la fiche est désormais produite pour tous les
    clients : le 500 tombait APRÈS l'écriture de la transition -- dossier
    marqué validé, mais ni fiche, ni contrat, ni DPAE, et rien dans le journal
    pour dire pourquoi."""
    uid = soumettre(c, base_saisie("ACME Restauration / Paris Opéra"))
    garde = config.societes()
    try:
        # l'établissement disparaît entre la soumission et la validation
        config._CACHE["societes"] = [
            {**s, "etablissements": [e for e in s["etablissements"]
                                     if e["nom"] != "Paris Opéra"]}
            for s in garde]
        assert not any(cle.endswith("Paris Opéra") for cle, _ in config.etablissements()), \
            "l'établissement n'a pas été retiré : le test ne prouverait rien"
        r = c.post(f"/dossier/{uid}/valider")
        assert r.status_code != 500, "500 sur /valider (établissement retiré)"
    finally:
        config._CACHE["societes"] = garde

    item = store.lire(uid)
    assert store.etat(item) != "Soumis", "la validation n'a pas eu lieu"
    echecs = [e for e in item["journal"] if e.get("type") == "fiche_echouee"]
    assert echecs, "échec de fiche non tracé dans le journal"
    assert "absent des sociétés" in echecs[-1]["motif"], echecs[-1]


def rejet_part_au_manager_de_l_etablissement(c):
    """Paris Opéra déclare manager_email : le rejet y part, pas à l'email saisi."""
    uid = soumettre(c, base_saisie("ACME Restauration / Paris Opéra"))
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Autre", "commentaire": "test adresse fixe"})
    ko = dernier_mail("rejet")
    assert ko.startswith("opera@example.com\n"), ko.splitlines()[0]


def recap_liste_la_semaine(uids):
    """Un mail PAR CABINET, RH en copie, un lien de lot vivant par dossier remis."""
    import recap
    seuil = recap.datetime.now(recap.timezone.utc) - recap.timedelta(days=7)
    remis = {i["id"] for i in recap.remis_depuis(seuil)}
    for uid in uids:
        assert uid in remis, f"{uid} absent du récap 7 jours"
    try:
        recap.main(7)
    except SystemExit as e:
        assert e.code == 0, "un mail hebdo n'est pas parti"
    envoyes = mails("recap_hebdo")
    assert len(envoyes) == 2, f"{len(envoyes)} mails hebdo pour 2 cabinets"
    par_cabinet = {}
    for msg in envoyes:
        assert msg["Cc"] == "rh@example.com", msg["Cc"]
        par_cabinet[msg["To"]] = corps_texte(msg)
    nord, sud = par_cabinet["cabinet-nord@example.com"], par_cabinet["cabinet-sud@example.com"]
    assert uids[0] in nord and uids[1] not in nord, "un cabinet voit l'autre société"
    assert uids[1] in sud and uids[0] not in sud
    assert "Camille MARTIN" in nord and "début" in nord
    lot = lien_dans(nord, "lot")
    assert app.test_client().get(lot).status_code == 200, "lien du mail hebdo mort"


def cas_base_indisponible(c):
    """La base lache pendant une requete RH : une page 503 lisible, jamais une
    trace -- y compris sur les actions sans try propre (DPAE faite)."""
    from pymongo.errors import AutoReconnect
    vraie = store.db

    def perdue():
        raise AutoReconnect("connexion perdue")
    store.db = perdue
    try:
        for r in (c.get("/suivi"),
                  c.post("/dossier/X/dpae-faite", data={}, content_type="multipart/form-data")):
            assert r.status_code == 503, r.status_code
            assert "Base de données indisponible" in r.get_data(as_text=True)
        # formulaire public envoyé en fetch : du JSON, que le JS affiche en
        # gardant les pièces choisies (une page HTML tombait dans « connexion ? »).
        # Compteur anti-flood vidé : plein, il répond un faux succès sans écrire.
        import app as module
        module._POT_PAR_IP.clear()
        r = app.test_client().post(
            "/demande", data={**base_saisie("ACME Restauration / Lille Grand Place"),
                       "identite": [piece(), piece()], "carte_vitale": piece(), "rib": piece()},
            content_type="multipart/form-data",
            headers={"X-Requested-With": "contratgen-fetch"})
        assert r.status_code == 503 and r.is_json, (r.status_code, r.get_data(as_text=True)[:200])
        assert r.get_json()["ok"] is False and "pièces sont conservées" in r.get_json()["erreurs"][0]
    finally:
        store.db = vraie
    assert c.get("/suivi").status_code == 200


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()

    # sans session, toute action RH est fermée
    assert c.get("/suivi").status_code == 302
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    assert c.get("/suivi").status_code == 200

    dk = couloir_dark_kitchen(c)
    resto = couloir_restaurant(c)
    fiche_generique_sans_cle(c)
    contrat_peut_citer_les_pieces_jointes(c)
    valider_survit_a_un_etablissement_retire(c)
    rejet_part_au_manager_de_l_etablissement(c)
    dpae_sans_signature(c)
    recap_liste_la_semaine([dk, resto])
    export_zip(c, dk, resto)
    cas_noms_du_zip()
    depart_leaver(c, dk)               # apres recap : Parti sort de remis_depuis()
    export_zip(c, dk, resto, apres_depart=True)
    cas_double_dpae()
    cas_course_champs()
    cas_dossier_inconnu_et_valider_apres_rejet()
    cas_base_indisponible(c)

    # lien forgé
    anonyme = app.test_client()
    assert anonyme.get("/lot/lot_comptable.X.1.0.deadbeef").status_code == 410

    print(f"\nDeux couloirs OK — Dark Kitchen {dk}, Restaurant {resto}")


if __name__ == "__main__":
    main()
