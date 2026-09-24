"""Le parcours complet, les deux couloirs du schéma, de bout en bout.

    python tests/test_parcours.py

Écrit dans un dossier temporaire, ne touche jamais data/.
  - couloir Dark Kitchen : Lille Grand Place, contrat généré
  - couloir Restaurant   : Marseille Prado, contrat déposé (myrhis)
Contrat signé facultatif, sans changer d'état. Fiche salarié générée dans contrat/ dès la
validation. recap.py liste le salarié de la semaine. Plus les refus attendus.
"""
import email
import os
import re
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEMP = tempfile.mkdtemp(prefix="contratgen-test-")
os.environ["DONNEES"] = TEMP

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

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
    fichiers = sorted((config.DONNEES / "mails").glob(f"*-{nom}.eml"))
    assert fichiers, f"aucun mail « {nom} » envoye"
    msg = email.message_from_bytes(fichiers[-1].read_bytes())
    return msg["To"] + "\n" + msg.get_payload(decode=True).decode("utf-8")


def lien_dans(mail, chemin):
    m = re.search(rf"(http://localhost/{chemin}/[\w.=-]+)", mail)
    assert m, f"pas de lien /{chemin} dans le mail :\n{mail}"
    return m.group(1)


def soumettre(c, saisie):
    r = c.post("/", data={**saisie, "identite": [piece(), piece()],
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
    avant = {d.name for d in (config.DONNEES / "soumissions").glob("*/")}
    r = c.post("/", data={**base_saisie("ACME Restauration / Lille Grand Place"),
                          "identite": piece(), "carte_vitale": piece(),
                          "rib": (BytesIO(b"PK"), "rib.docx")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and "format .docx refusé" in r.text, \
        (r.status_code, r.text[:300])
    apres = {d.name for d in (config.DONNEES / "soumissions").glob("*/")}
    assert apres == avant, f"soumission orpheline créée : {apres - avant}"


def couloir_dark_kitchen(c):
    """Lille Grand Place — contrat généré par l'app, signature manuelle."""
    piece_refusee(c)
    uid = soumettre(c, base_saisie("ACME Restauration / Lille Grand Place"))

    # KO puis correction
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": "Pièce illisible ou manquante", "commentaire": "RIB flou"})
    assert store.etat(store.lire(uid)) == "Rejetee"
    ko = dernier_mail("rejet")
    assert ko.startswith("manager@example.com\n"), \
        "établissement sans manager_email : le rejet doit retomber sur l'email saisi"
    lien = lien_dans(ko, "corriger")
    r = c.post(lien, data={**base_saisie("ACME Restauration / Lille Grand Place"),
                           "rib": piece()}, content_type="multipart/form-data")
    assert "Correction envoyée" in r.text
    assert c.get(lien).status_code == 410, "lien de correction encore vivant"
    assert (Path(store.lire(uid)["_dir"]) / "_versions").is_dir(), "re-dépôt non archivé"

    # validation -> dossier + fiche salarié + contrat généré dans la foulée
    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    assert item["_zone"] == "documents" and store.etat(item) == "ContratPret"
    assert Path(item["_dir"]).relative_to(config.DONNEES).as_posix() == \
        "DOSSIERS SALARIES/DARK KITCHENS/Lille Grand Place/Manager/MARTIN Camille", item["_dir"]
    assert store.chemin(uid, "pieces").name == "FICHE PERSONNELLE"
    assert store.chemin(uid, "contrat").name == "CONTRAT"
    store._carte.clear()                      # uid -> chemin par le seul disque
    assert store.lire(uid)["_dir"] == item["_dir"], "scan de resolution KO"
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
    doc = Document(str(store.chemin(item["id"], "contrat", nom_ct)))
    texte = "\n".join(p.text for p in moteur.paragraphes(doc))
    assert "{{" not in texte, "contrat troué"
    for attendu in ("Camille MARTIN", "1er octobre 2026", "2450",
                    "ACME RESTAURATION SAS", "884 512 336 00027", "Lille Grand Place"):
        assert attendu in texte, f"« {attendu} » absent du contrat"

    # signature manuelle : dépôt du PDF signé, sans changer d'état
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret", "le signé a changé l'état"

    # DPAE faite (accusé obligatoire)
    c.post(f"/dossier/{uid}/dpae-faite", data={}, content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret", "DPAE validée sans accusé"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"

    # remise : plus de mail par dossier -- copie dans data/compta/, lien dans
    # le mail hebdo. Le lot porte contrat, contrat signé, fiche salarié, accusé.
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    assert not list((config.DONNEES / "mails").glob("*-avis_comptable.eml")), \
        "un mail par dossier est encore parti à la remise"
    # Arbo COMPTA : profonde (GROUPE/ETABLISSEMENT/POSTE/NOM PRENOM - id) et en
    # capitales sans accents ; chaque fichier renommé « <libellé> - Nom - date ».
    copie = (config.DONNEES / "COMPTA" / "DARK KITCHENS" / "LILLE GRAND PLACE"
             / "MANAGER" / f"MARTIN CAMILLE - {uid}")
    assert copie.is_dir(), f"arbo compta absente : {copie}"
    attendu = sorted(f"{store.BUCKETS_COMPTA[b]}/{store.nom_export(item, b, n)}"
                     for b in ("pieces", "contrat") for n in store.fichiers(item, b))
    presents = sorted(p.relative_to(copie).as_posix()
                      for p in copie.rglob("*") if p.is_file())
    assert presents == attendu, (presents, attendu)
    assert all(re.search(r" - MARTIN Camille - \d{4}-\d{2}-\d{2}\.\w+$", n)
               for n in presents), presents
    assert {"FICHE PERSONNELLE", "CONTRAT"} == {n.split("/")[0] for n in presents}
    lot = "http://localhost/lot/" + store.signer("lot_comptable", uid,
                                                 item.get("lien_comptable_epoch", 0))
    anonyme = app.test_client()
    z = zipfile.ZipFile(BytesIO(anonyme.get(lot + "/zip").data))
    assert sorted(z.namelist()) == attendu, z.namelist()

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
    """Marseille Prado — contrat fait sur myrhis puis déposé."""
    uid = soumettre(c, base_saisie("ACME Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    c.post(f"/dossier/{uid}/valider")
    assert store.etat(store.lire(uid)) == "ATraiter"

    # refus : « Générer le contrat » sur un établissement en mode déposé
    r = c.post(f"/dossier/{uid}/contrat", follow_redirects=True)
    assert store.etat(store.lire(uid)) == "ATraiter", "contrat généré sur un établissement déposé"
    assert "contrat déposé" in r.text or "myrhis" in r.text

    # dépôt du contrat -> ContratPret
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "ContratPret", item["journal"][-1]
    assert store.fichiers_role(item, "contrat", "contrat") == \
        ["Contrat - MARTIN Camille.pdf"], store.fichiers(item, "contrat")

    # signature manuelle puis DPAE directe (sans rappel)
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratPret"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"
    assert (config.DONNEES / "COMPTA" / "RESTAURANTS" / "MARSEILLE PRADO").is_dir()
    return uid


def dpae_sans_signature(c):
    """DPAE directe depuis ContratPret, contrat signé facultatif (déposé
    après, sans changer d'état), remise sans lui."""
    uid = soumettre(c, base_saisie("ACME Sud / Marseille Prado",
                                   poste="Équipier polyvalent"))
    # refus : pas de contrat signé avant que le contrat soit prêt
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
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    item = store.lire(uid)
    assert store.etat(item) == "DpaeFaite", "le dépôt du contrat signé a changé l'état"
    assert store.fichiers_role(item, "contrat", "contrat-signe"), store.fichiers(item, "contrat")
    assert item["journal"][-1]["type"] == "contrat_signe", item["journal"][-1]
    # un second reçu est refusé : ni écrasement, ni seconde entrée de journal
    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()},
           content_type="multipart/form-data")
    apres = store.lire(uid)
    assert len(apres["journal"]) == len(item["journal"]), apres["journal"][-1]
    assert store.fichiers(apres, "contrat") == store.fichiers(item, "contrat")
    c.post(f"/dossier/{uid}/remettre")
    assert store.etat(store.lire(uid)) == "RemisComptable"

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


def depart_leaver(c, uid):
    """Départ d'un salarié remis au comptable : déplacement réel vers LEAVERS/,
    rejouable si l'écriture du journal échoue APRÈS le move (disque plein...),
    visible seulement dans /salaries?anciens=1, écran détail sans 500, et un
    second archivage refusé proprement une fois Parti (pas de 500)."""
    item = store.lire(uid)
    avant = Path(item["_dir"])
    rel = avant.relative_to(config.DONNEES / store.DOSSIERS)

    # Rejouable (store.archiver) : un echec d'ecriture apres le move laisse le
    # dossier sous LEAVERS/ mais encore a RemisComptable -- le second appel ne
    # doit pas re-deplacer, seulement rejouer l'ecriture.
    reel_ecrire = store._ecrire
    store._ecrire = lambda *a: (_ for _ in ()).throw(OSError("disque plein (test)"))
    try:
        try:
            store.archiver(uid, "test", "2026-09-30")
            assert False, "l'échec d'écriture simulé n'a pas levé"
        except OSError:
            pass
    finally:
        store._ecrire = reel_ecrire
    store._carte.clear()
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable", "état changé malgré l'échec d'écriture"
    assert Path(item["_dir"]) == config.DONNEES / store.LEAVERS / rel, \
        "dossier non déplacé malgré l'échec d'écriture"
    assert not avant.exists(), "ancien répertoire encore présent"

    c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "2026-09-30"})
    item = store.lire(uid)
    assert store.etat(item) == "Parti", item["journal"][-1]
    assert not avant.exists(), "ancien répertoire encore sous DOSSIERS SALARIES"
    apres = Path(item["_dir"])
    assert apres == config.DONNEES / store.LEAVERS / rel, apres

    assert c.get("/suivi").text.find(uid) == -1, "encore visible dans /suivi"
    assert uid not in c.get("/salaries").text, "encore dans les salariés en poste"
    assert uid in c.get("/salaries?anciens=1").text, "absent de /salaries?anciens=1"

    r = c.get(f"/dossier/{uid}")
    assert r.status_code == 200, "500 sur l'écran détail (garde flux.index)"

    r = c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "2026-10-01"},
               follow_redirects=True)
    assert r.status_code == 200, "second archivage : 500 au lieu d'un refus propre"
    assert store.etat(store.lire(uid)) == "Parti"


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
        doc = Document(str(store.chemin(uid, "pieces", noms[0])))
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
            Document(str(store.chemin(uid, "contrat", noms[0])))))
        assert "{{" not in texte, f"contrat troué : {texte}"
        assert "Pièces fournies :" in texte, texte
    finally:
        config.instance()["templates"] = garde
        modele.unlink(missing_ok=True)


def valider_survit_a_un_etablissement_retire(c):
    """Un établissement fermé, retiré de societes.json, alors qu'un vieux
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
        config._CACHE["societes.json"] = [
            {**s, "etablissements": [e for e in s["etablissements"]
                                     if e["nom"] != "Paris Opéra"]}
            for s in garde]
        assert not any(cle.endswith("Paris Opéra") for cle, _ in config.etablissements()), \
            "l'établissement n'a pas été retiré : le test ne prouverait rien"
        r = c.post(f"/dossier/{uid}/valider")
        assert r.status_code != 500, "500 sur /valider (établissement retiré)"
    finally:
        config._CACHE["societes.json"] = garde

    item = store.lire(uid)
    assert store.etat(item) != "Soumis", "la validation n'a pas eu lieu"
    echecs = [e for e in item["journal"] if e.get("type") == "fiche_echouee"]
    assert echecs, "échec de fiche non tracé dans le journal"
    assert "societes.json" in echecs[-1]["motif"], echecs[-1]


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
    fichiers = sorted((config.DONNEES / "mails").glob("*-recap_hebdo.eml"))
    assert len(fichiers) == 2, f"{len(fichiers)} mails hebdo pour 2 cabinets"
    par_cabinet = {}
    for f in fichiers:
        msg = email.message_from_bytes(f.read_bytes())
        assert msg["Cc"] == "rh@example.com", msg["Cc"]
        par_cabinet[msg["To"]] = msg.get_payload(decode=True).decode("utf-8")
    nord, sud = par_cabinet["cabinet-nord@example.com"], par_cabinet["cabinet-sud@example.com"]
    assert uids[0] in nord and uids[1] not in nord, "un cabinet voit l'autre société"
    assert uids[1] in sud and uids[0] not in sud
    assert "Camille MARTIN" in nord and "début" in nord
    lot = lien_dans(nord, "lot")
    assert app.test_client().get(lot).status_code == 200, "lien du mail hebdo mort"


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
    depart_leaver(c, dk)               # apres recap : Parti sort de remis_depuis()

    # lien forgé
    anonyme = app.test_client()
    assert anonyme.get("/lot/lot_comptable.X.1.0.deadbeef").status_code == 410

    print(f"\nDeux couloirs OK — Dark Kitchen {dk}, Restaurant {resto}\n{TEMP}")


if __name__ == "__main__":
    main()
