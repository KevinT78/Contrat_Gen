"""Conservation et purge : un dossier terminé finit par oublier ses pièces.

    python tests/test_purge.py

Le test qui vaut tous les autres : dérouler un parcours complet jusqu'à
RemisComptable, purger, puis affirmer que le nom du salarié (et le NIR, et
l'adresse) n'apparaît dans AUCUN document des collections `dossiers` et
`fichiers` (octets compris) — hors collection `mails`, carve-out documenté
(purge par ancienneté, pas par dossier). Puis rejouer la purge : ni exception,
ni changement.

Écrit dans une base vide, rien sur le disque.
"""
import email
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo.errors import PyMongoError

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo  # noqa: E402
base_mongo("purge")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402
from _outils import compte  # noqa: E402
compte("rh", "fixture")
from _outils import corps_texte, mails  # noqa: E402


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF", "p.pdf")
NOM, PRENOM = "Delacroix-Nkemba", "Eugénie"
NIR = "2 91 07 75 116 001 38"
ADRESSE = "88 rue des Amandiers, 75020 Paris"
PII = [NOM, NOM.upper(), PRENOM, NIR, ADRESSE, "116 001"]


def piece():
    return (io.BytesIO(PDF[0]), PDF[1])


def saisie(etab="ACME Restauration / Lille Grand Place", poste="Manager"):
    return {
        "etablissement": etab, "email_demandeur": "manager@example.com",
        "nom_naissance": NOM, "nom_usage": "", "prenom": PRENOM,
        "date_naissance": "1991-07-02", "lieu_naissance": "Paris",
        "nationalite": "Française", "num_secu": NIR, "adresse": ADRESSE,
        "poste": poste, "type_contrat": "CDI", "date_debut": "2026-10-01",
        "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2600",
    }


def soumettre(c, s):
    r = c.post("/demande", data={**s, "identite": [piece(), piece()],
                          "carte_vitale": piece(), "rib": piece()},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())


def dernier_mail(nom):
    envoyes = mails(nom)
    assert envoyes, f"aucun mail « {nom} »"
    msg = envoyes[-1]
    return corps_texte(msg)


def parcours_jusqua_remis(c):
    """Soumission → KO (commentaire libre + ip à jeter) → correction → validation
    → contrat → signature → DPAE → remise. Rend l'uid, en RemisComptable."""
    uid = soumettre(c, saisie())
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante",
                 "commentaire": f"COMMENTAIRE-LIBRE-{NOM}-doit-disparaitre"})
    lien = re.search(r"http://localhost(/corriger/[\w.=-]+)", dernier_mail("rejet"))
    assert lien, "pas de lien de correction dans le mail de rejet"
    r = c.post(lien.group(1), data={**saisie(), "rib": piece()},
               content_type="multipart/form-data")
    assert "Correction envoyée" in r.text, r.text[:300]

    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ContratPret"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{uid}/remettre")
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable", item["journal"][-1]
    # une note récente APRÈS l'entrée dans l'état : _entree_etat_courant ne doit
    # pas la prendre pour la date de référence.
    store.noter(uid, type="acces_lot", par=None, ip="203.0.113.9")
    return uid


def antidater_etat_courant(uid, jours):
    """Recule la date de l'entrée qui a fait entrer dans l'état courant."""
    item = store.lire(uid)
    courant = store.etat(item)
    vieux = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat(timespec="seconds")
    for e in item["journal"]:
        if e.get("vers") == courant and e.get("de") != e.get("vers"):
            e["le"] = vieux
    store.db().dossiers.update_one({"_id": uid}, {"$set": {"journal": item["journal"]}})


def octets_et_textes():
    """(octets, textes) de tout ce qui reste : chaque document `dossiers` (en
    JSON), chaque fichier (nom et octets)."""
    blobs, textes = [], []
    for d in store.db().dossiers.find():
        textes.append(json.dumps(d, ensure_ascii=False, default=str))
    for f in store.db().fichiers.find():
        textes.append(f["nom"])
        blobs.append(bytes(f["octets"]))
    return blobs, textes


def brut(uid):
    return store.db().dossiers.find_one({"_id": uid}), store.db().fichiers.count_documents({"uid": uid})


def main():
    assert not config.verifier(), config.verifier()
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    assert c.get("/suivi").status_code == 200

    remis = parcours_jusqua_remis(c)
    # Dossiers témoins : données PROPRES (aucune aiguille de PII), pour que la
    # recherche « le NIR n'apparaît nulle part » ne bute pas sur un non-purgé.
    temoin = {"num_secu": "1 91 07 99 999 999 82", "adresse": "1 rue Neutre, 00000 Nulle"}
    # une soumission jamais validée, pour la durée « candidature »
    soumise = soumettre(c, {**saisie(poste="Assistant manager"), **temoin,
                            "nom_naissance": "Temoin", "prenom": "Sam"})
    # un dossier remis récent : sous le seuil, ne doit PAS être éligible
    recent = soumettre(c, {**saisie(poste="Assistant manager"), **temoin,
                           "nom_naissance": "Recent", "prenom": "Max"})
    c.post(f"/dossier/{recent}/valider")
    c.post(f"/dossier/{recent}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    c.post(f"/dossier/{recent}/remettre")
    c.post(f"/dossier/{recent}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")

    config.instance()["conservation"] = {
        "jours": 30, "jours_candidature": 10,
        "apres": ["RemisComptable", "Rejetee", "Abandonnee", "Soumise"]}
    try:
        antidater_etat_courant(remis, 400)
        antidater_etat_courant(soumise, 40)
        antidater_etat_courant(recent, 15)      # < 30 j : pas éligible

        apercu = c.get("/purger")
        assert apercu.status_code == 200 and "2 dossier(s) à purger" in apercu.get_data(as_text=True)
        assert c.get("/suivi").status_code == 200 and \
            "dépassé leur durée de conservation" in c.get("/suivi").get_data(as_text=True)

        elig = {i["id"]: (e, d) for i, e, d in store.eligibles()}
        assert set(elig) == {remis, soumise}, f"éligibles inattendus : {set(elig)}"
        assert elig[remis][1] == 30 and elig[soumise][1] == 10, elig
        assert elig[remis][0] >= 395, "note d'accès prise pour la date d'entrée"

        # purger.py en simulation : sa sortie finit dans le journal systemd du
        # VPS, donc sur disque. Les dossiers y sont, leurs titulaires non.
        import contextlib
        import purger
        sortie, code = io.StringIO(), None
        with contextlib.redirect_stdout(sortie):
            try:
                purger.main()
            except SystemExit as e:
                code = e.code
        assert code == 1, f"des dossiers à purger : code 1 attendu, pas {code}"
        sortie = sortie.getvalue()
        assert remis in sortie and soumise in sortie and recent not in sortie, sortie
        assert "Lille Grand Place" in sortie, sortie
        for fuite in PII + ["Temoin", "Sam"]:
            assert fuite not in sortie, f"« {fuite} » dans la sortie de purger.py"

        # jeton de lot émis AVANT la purge
        item = store.lire(remis)
        lot = "/lot/" + store.signer("lot_comptable", remis,
                                     item.get("lien_comptable_epoch", 0))
        assert app.test_client().get(lot).status_code == 200

        # --- base qui lâche pendant la boucle : un flash, pas un 500, rien
        #     de purgé, et la boucle reste rejouable (passage suivant) ---
        vraie_purge, vraie_db = store.purger, store.db

        def perdue():
            raise PyMongoError("hote-secret.exemple:27017 timed out")

        def purge_sans_base(uid):           # la vraie purge, base coupee dessous
            store.db = perdue
            try:
                return vraie_purge(uid)
            finally:
                store.db = vraie_db
        store.purger = purge_sans_base
        try:
            r = c.post("/purger", follow_redirects=True)
        finally:
            store.purger = vraie_purge
        page = r.get_data(as_text=True)
        assert r.status_code == 200 and "purge incomplète" in page, r.status_code
        assert "hote-secret" not in page, "le message brut de l'erreur Mongo est affiché"
        assert not store.lire(remis).get("purge")
        assert store.db().fichiers.count_documents({"uid": remis}), "pièces effacées malgré l'échec"

        # --- la purge, par la route ---
        r = c.post("/purger", follow_redirects=True)
        assert r.status_code == 200 and "2 dossier(s) purgé" in r.get_data(as_text=True), r.text[:400]

        # 1. le nom / NIR / adresse : nulle part en base (hors collection mails)
        blobs, textes = octets_et_textes()
        for aiguille in PII:
            assert not any(aiguille in t for t in textes), \
                f"« {aiguille} » dans un document, un nom de fichier ou un chemin"
            b = aiguille.encode("utf-8")
            assert not any(b in blob for blob in blobs), f"« {aiguille} » dans un fichier"
        # carve-out assumé : les .eml, eux, portent encore le nom (purge par
        # ancienneté, pas par dossier — cf. purger.py et docs/ADR-stockage.md)
        eml = b"".join(d["eml"] for d in store.db().mails.find())
        assert NOM.encode("utf-8") in eml, \
            "le carve-out collection mails n'est plus vrai — revoir le test et la doc"

        # 2. pièces effacées (_versions comprises), marqueur, champs réduits, journal nettoyé
        item = store.lire(remis)
        assert store.db().fichiers.count_documents({"uid": remis}) == 0
        assert item["purge"]["jours"] == 30 and item["purge"]["le"]
        assert set(item["champs"]) <= {config.role("etablissement"), config.role("poste"),
                                       config.role("date_debut")}, item["champs"]
        assert store.etat(item) == "RemisComptable", "un état « Purgee » a été créé"
        assert item["journal"][-1]["type"] == "purge"
        assert item["journal"][-1]["de"] == item["journal"][-1]["vers"] == "RemisComptable"
        assert not any("commentaire" in e or "ip" in e for e in item["journal"]), \
            "commentaire libre ou ip resté dans le journal"
        assert any(store.est_remise(e) and e.get("jour") for e in item["journal"]), \
            "jour local de la remise perdu à la purge : la date repasserait en UTC"

        # 3. epochs bumpés : le jeton d'avant la purge est mort
        assert app.test_client().get(lot).status_code == 410, "jeton de lot survivant"

        # 4. manquantes() : garde actif -> [] (sinon /suivi vire au rouge faux)
        assert store.manquantes(item) == []
        sans_marqueur = {k: v for k, v in item.items() if k != "purge"}
        assert store.manquantes(sans_marqueur), \
            "contrôle négatif : sans la clé purge, manquantes() doit lister les pièces effacées"

        # 5. affichable sans 500
        assert c.get("/suivi").status_code == 200
        assert c.get(f"/dossier/{remis}").status_code == 200
        assert c.get(f"/dossier/{soumise}").status_code == 200

        # 6. soumission purgée : pièces effacées, toujours listée
        assert store.db().fichiers.count_documents({"uid": soumise}) == 0
        assert soumise in {i["id"] for i in store.tout()}

        # 7. rejouable : deux passages de plus, ni exception ni changement
        #    (le marqueur `purge` est là → reprise directe aux effacements,
        #    sans re-vérifier l'éligibilité)
        avant = brut(remis)
        store.purger(remis)
        store.purger(remis)
        assert brut(remis) == avant, "un second passage de purge a modifié le dossier"

        # 8. le dossier récent (15 j) n'a pas été touché — et purger() appelé
        #    directement dessus revérifie l'éligibilité et sort sans rien faire
        #    (garde anti-TOCTOU : la liste de l'écran peut avoir vieilli).
        avant_recent = brut(recent)
        assert avant_recent[1], "contrôle : le dossier récent a des pièces"
        store.purger(recent)
        ritem = store.lire(recent)
        assert not ritem.get("purge"), "dossier récent purgé à tort"
        assert brut(recent) == avant_recent, \
            "purger() a touché un dossier sous le seuil de conservation"
        assert config.identite(ritem["champs"]) == ("Max", "Recent"), ritem["champs"]

        # 9. le dossier bouge entre la lecture et l'écriture de la purge (le
        #    cabinet ouvre le lot) : la mise à jour conditionnée ne s'applique
        #    pas, rien n'est effacé ; le passage suivant purge.
        antidater_etat_courant(recent, 40)
        vraie = store._eligibilite

        def eligibilite_puis_note(item, conf):
            store.noter(item["id"], type="acces_lot", par=None)
            return vraie(item, conf)
        store._eligibilite = eligibilite_puis_note
        try:
            store.purger(recent)
        finally:
            store._eligibilite = vraie
        doc, n = brut(recent)
        assert "purge" not in doc and n == avant_recent[1], "purgé malgré un journal qui a bougé"
        store.purger(recent)
        doc, n = brut(recent)
        assert doc.get("purge") and n == 0, "le passage suivant n'a pas purgé"
    finally:
        config.instance().pop("conservation", None)

    print(f"\nPurge OK — remis {remis} vidé, soumission {soumise} vidée, "
          f"récent {recent} intact sous le seuil, épargné quand il bouge.")


if __name__ == "__main__":
    main()
