"""Conservation et purge : un dossier terminé finit par oublier ses pièces.

    python tests/test_purge.py

Le test qui vaut tous les autres : dérouler un parcours complet jusqu'à
RemisComptable, purger, puis affirmer que le nom du salarié (et le NIR, et
l'adresse) n'apparaît dans AUCUN octet ni AUCUN nom de chemin sous
config.DONNEES — hors data/mails/, carve-out documenté (purge par ancienneté,
pas par dossier). Puis rejouer la purge : ni exception, ni changement.

Écrit dans un dossier temporaire, ne touche jamais data/.
"""
import email
import io
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEMP = tempfile.mkdtemp(prefix="contratgen-purge-")
os.environ["DONNEES"] = TEMP

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

(config.DONNEES / "soumissions").mkdir(parents=True, exist_ok=True)

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF", "p.pdf")
NOM, PRENOM = "Delacroix-Nkemba", "Eugénie"
NIR = "2 91 07 75 116 001 42"
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
    r = c.post("/", data={**s, "identite": [piece(), piece()],
                          "carte_vitale": piece(), "rib": piece()},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "Demande envoyée" in r.text, r.status_code
    return max(i["id"] for i in store.tout())


def dernier_mail(nom):
    fichiers = sorted((config.DONNEES / "mails").glob(f"*-{nom}.eml"))
    assert fichiers, f"aucun mail « {nom} »"
    msg = email.message_from_bytes(fichiers[-1].read_bytes())
    return msg.get_payload(decode=True).decode("utf-8")


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
    d = Path(item["_dir"])
    courant = store.etat(item)
    vieux = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat(timespec="seconds")
    for e in item["journal"]:
        if e.get("vers") == courant and e.get("de") != e.get("vers"):
            e["le"] = vieux
    store._ecrire(d, item)
    store._carte.clear()


def octets_et_chemins():
    """(tous les octets de fichiers, tous les noms de chemins) sous DONNEES,
    hors data/mails/ (carve-out : purge par ancienneté, pas par dossier)."""
    blobs, chemins = [], []
    for p in config.DONNEES.rglob("*"):
        if "mails" in p.relative_to(config.DONNEES).parts:
            continue
        chemins.append(str(p))
        if p.is_file():
            blobs.append(p.read_bytes())
    return blobs, chemins


def main():
    assert not config.verifier(), config.verifier()
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    assert c.get("/suivi").status_code == 200

    remis = parcours_jusqua_remis(c)
    # Dossiers témoins : données PROPRES (aucune aiguille de PII), pour que la
    # recherche « le NIR n'apparaît nulle part » ne bute pas sur un non-purgé.
    temoin = {"num_secu": "1 80 01 99 999 999 88", "adresse": "1 rue Neutre, 00000 Nulle"}
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

        # jeton de lot émis AVANT la purge
        item = store.lire(remis)
        lot = "/lot/" + store.signer("lot_comptable", remis,
                                     item.get("lien_comptable_epoch", 0))
        assert app.test_client().get(lot).status_code == 200

        # --- la purge, par la route ---
        r = c.post("/purger", follow_redirects=True)
        assert r.status_code == 200 and "2 dossier(s) purgé" in r.get_data(as_text=True), r.text[:400]

        # 1. le nom / NIR / adresse : nulle part sous DONNEES (hors mails)
        blobs, chemins = octets_et_chemins()
        for aiguille in PII:
            assert not any(aiguille in ch for ch in chemins), \
                f"« {aiguille} » dans un nom de chemin"
            b = aiguille.encode("utf-8")
            assert not any(b in blob for blob in blobs), f"« {aiguille} » dans un fichier"
        # carve-out assumé : les .eml, eux, portent encore le nom (purge par
        # ancienneté, pas par dossier — cf. purger.py et docs/ADR-stockage.md)
        eml = b"".join(f.read_bytes() for f in (config.DONNEES / "mails").glob("*.eml"))
        assert NOM.encode("utf-8") in eml, \
            "le carve-out data/mails/ n'est plus vrai — revoir le test et la doc"

        # 2. répertoire renommé, marqueur, champs réduits, journal nettoyé
        item = store.lire(remis)
        assert Path(item["_dir"]).name == f"purge-{remis}"
        assert item["purge"]["jours"] == 30 and item["purge"]["le"]
        assert set(item["champs"]) <= {config.role("etablissement"), config.role("poste"),
                                       config.role("date_debut")}, item["champs"]
        assert store.etat(item) == "RemisComptable", "un état « Purgee » a été créé"
        assert item["journal"][-1]["type"] == "purge"
        assert item["journal"][-1]["de"] == item["journal"][-1]["vers"] == "RemisComptable"
        assert not any("commentaire" in e or "ip" in e for e in item["journal"]), \
            "commentaire libre ou ip resté dans le journal"

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

        # 6. soumission purgée : répertoire NON renommé (ULID = pas de PII),
        #    buckets vides, toujours vue par _scan
        sitem = store.lire(soumise)
        assert Path(sitem["_dir"]).name == soumise, "une soumission a été renommée"
        assert not (Path(sitem["_dir"]) / "FICHE PERSONNELLE").exists()
        assert soumise in store._scan()

        # 7. rejouable : deux passages de plus, ni exception ni changement
        #    (le marqueur `purge` est là → reprise directe aux effacements,
        #    sans re-vérifier l'éligibilité)
        avant = store._fichier_json(Path(item["_dir"])).read_bytes()
        store.purger(remis)
        store.purger(remis)
        assert store._fichier_json(Path(store.lire(remis)["_dir"])).read_bytes() == avant, \
            "un second passage de purge a modifié le dossier"

        # 8. le dossier récent (15 j) n'a pas été touché — et purger() appelé
        #    directement dessus revérifie l'éligibilité et sort sans rien faire
        #    (garde anti-TOCTOU : la liste de l'écran peut avoir vieilli).
        avant_recent = store._fichier_json(Path(store.lire(recent)["_dir"])).read_bytes()
        store.purger(recent)
        ritem = store.lire(recent)
        assert not ritem.get("purge"), "dossier récent purgé à tort"
        assert store._fichier_json(Path(ritem["_dir"])).read_bytes() == avant_recent, \
            "purger() a touché un dossier sous le seuil de conservation"
        assert config.identite(ritem["champs"]) == ("Max", "Recent"), ritem["champs"]
    finally:
        config.instance().pop("conservation", None)

    print(f"\nPurge OK — remis {remis} vidé, soumission {soumise} vidée, "
          f"récent {recent} intact.\n{TEMP}")


if __name__ == "__main__":
    main()
