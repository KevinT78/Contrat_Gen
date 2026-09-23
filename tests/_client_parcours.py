"""Driver : joue le parcours complet dans UNE copie du dépôt déjà installée.

    python tests/_client_parcours.py <racine-copie> <spec.json>

Lancé en sous-processus par tests/test_clients.py, une fois par client factice.
Sort 0 si le parcours passe, imprime « PARCOURS OK <client> <secret8> <donnees> ».
"""
import email
import html
import io
import json
import re
import sys
import zipfile
from pathlib import Path

RACINE = sys.argv[1]
SPEC = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
sys.path.insert(0, RACINE)
sys.stdout.reconfigure(encoding="utf-8")

import config          # noqa: E402
import doctor          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

# instance.json -> url : base des liens _external que l'app fabrique DANS une
# requete (url_for(..., _external=True)). L'app les construit depuis le Host
# de la requete recue, pas depuis cette config -- correct en prod (reverse
# proxy = vrai Host), mais le client de test n'envoie aucun Host et retombe
# sur "http://localhost" sans port. On le corrige ICI, cote test, en donnant
# base_url a chaque requete qui declenche un mail : ce n'est pas un defaut de
# l'app, url_publique() n'est d'ailleurs faite QUE pour hors-requete (recap.py).
BASE_URL = config.url_publique() or None


def f():
    return (io.BytesIO(b"%PDF-1.4\n%%EOF"), "p.pdf")


def saisie(etab, poste):
    """Le formulaire du CLIENT, pas une saisie codee en dur : les ids de champs
    varient d'une instance a l'autre, seuls les roles sont surs. C'est ce que ce
    driver doit prouver -- une saisie ecrite en dur testerait un seul nommage."""
    vals = doctor.salarie_fictif()
    vals[config.role("etablissement")] = etab
    vals[config.role("poste")] = poste
    vals[config.role("email")] = "manager@example.com"
    vals[config.role("nom")] = "Nkemba"
    # identite() prefere nom_usage a nom_naissance : doctor.salarie_fictif()
    # remplit nom_usage avec la valeur fictive generique « Exemple », qui
    # masquerait « Nkemba » ci-dessus. Vide comme la majorite des salaries
    # (aide du champ : « si different »).
    vals[config.role("nom_usage")] = ""
    return vals


def couloir(c, etab, mode, poste, attendus):
    r = c.post("/", data={**saisie(etab, poste),
                          **{p["id"]: [f() for _ in range(p.get("max_fichiers", 1))] for p in config.pieces()}},
               content_type="multipart/form-data")
    assert "Demande envoyée" in r.text, r.text[:300]
    uid = max(i["id"] for i in store.tout())

    c.post(f"/dossier/{uid}/valider")
    item = store.lire(uid)
    # Contrat généré dès la validation (couloir « genere ») ; le couloir
    # « depose » reste à ATraiter en attendant le dépôt du PDF myrhis.
    # Avec une saisie_rh, la validation attend la RH : on génère avec ses valeurs.
    saisie_rh = mode == "genere" and config.saisie_rh()
    assert store.etat(item) == ("ContratPret" if mode == "genere" and not saisie_rh
                                else "ATraiter"), store.etat(item)
    if saisie_rh:
        valeurs = {p: SPEC.get("saisie_rh", {}).get(p, f"essai {p}") for p in saisie_rh}
        c.post(f"/dossier/{uid}/contrat", data=valeurs)
        item = store.lire(uid)
        assert store.etat(item) == "ContratPret", f"saisie RH : {store.etat(item)}"

    # Les ECRANS aussi doivent lire par role : chez un client qui renomme ses
    # champs, « item.champs.poste » en dur sortait une colonne VIDE, en silence.
    for page in (f"/dossier/{uid}", "/suivi"):
        # unescape : « Chef d'atelier » sort en « Chef d&#39;atelier » dans le
        # HTML. On asserte sur le texte tel qu'un humain le lit, pas sur
        # l'echappement -- sinon tout intitule a apostrophe fait un faux rouge.
        txt = html.unescape(c.get(page).text)
        assert poste in txt, f"« {poste} » absent de {page} — id lu en dur ?"
        assert etab in txt, f"« {etab} » absent de {page} — id lu en dur ?"
    # Toujours produite : modele du client si declare, generique sinon. Sans
    # cle client, le modele generique (verse avec le code, pas config/) doit
    # rendre les reponses du formulaire sans jeton troue (lignes sans source
    # retirees, contrat.elaguer).
    noms_fiche = store.fichiers_role(item, "pieces", "fiche-salarie")
    assert noms_fiche, "fiche absente"
    if not config.instance().get("fiche_salarie"):
        from docx import Document
        import contrat as moteur
        doc = Document(str(store.chemin(item["id"], "pieces", noms_fiche[0])))
        texte = "\n".join(p.text for p in moteur.paragraphes(doc))
        assert "{{" not in texte, "fiche générique trouée"
        # etab = « Societe / Etablissement » : la fiche montre l'etablissement
        assert poste in texte and etab.split(" / ")[-1] in texte, \
            f"fiche générique : « {poste} »/« {etab} » absents"

    if mode == "genere":
        from docx import Document
        import contrat as moteur
        nom_ct = store.fichiers_role(item, "contrat", "contrat")[0]
        doc = Document(str(store.chemin(item["id"], "contrat", nom_ct)))
        texte = "\n".join(p.text for p in moteur.paragraphes(doc))
        assert "{{" not in texte, "contrat troué"
        for a in attendus:
            assert a in texte, f"« {a} » absent du contrat de {etab}"
    else:
        r = c.post(f"/dossier/{uid}/contrat", follow_redirects=True)
        assert store.etat(store.lire(uid)) == "ATraiter", "généré sur un étab. déposé"
        c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": f()},
               content_type="multipart/form-data")
        assert store.etat(store.lire(uid)) == "ContratPret"

    c.post(f"/dossier/{uid}/contrat-signe", data={"signe": f()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "ContratSigne"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": f()},
           content_type="multipart/form-data")
    assert store.etat(store.lire(uid)) == "DpaeFaite"
    c.post(f"/dossier/{uid}/remettre")
    item = store.lire(uid)
    assert store.etat(item) == "RemisComptable"

    jeton = store.signer("lot_comptable", uid, item.get("lien_comptable_epoch", 0))
    z = zipfile.ZipFile(io.BytesIO(app.test_client().get(f"/lot/{jeton}/zip").data))
    assert f"CONTRAT/{store.nom_export(item, 'contrat', 'contrat-signe.pdf')}" \
        in z.namelist(), z.namelist()
    lot = html.unescape(app.test_client().get(f"/lot/{jeton}").text)
    assert poste in lot and etab in lot, "page du lot comptable : champ lu en dur"
    return uid


def dernier_mail(nom):
    """Corps (To + texte) du dernier .eml « <horodatage>-<serie>-<nom>.eml »
    ecrit par mails.py en mode console -- meme lecture que test_parcours.py."""
    fichiers = sorted((config.DONNEES / "mails").glob(f"*-{nom}.eml"))
    assert fichiers, f"aucun mail « {nom} » envoyé"
    msg = email.message_from_bytes(fichiers[-1].read_bytes())
    return msg["To"] + "\n" + msg.get_payload(decode=True).decode("utf-8")


def lien_dans(mail, chemin):
    base = re.escape(BASE_URL) if BASE_URL else r"https?://[\w.:-]+"
    m = re.search(rf"({base}/{chemin}/[\w.=-]+)", mail)
    assert m, f"pas de lien /{chemin} dans le mail :\n{mail}"
    return m.group(1)


def refuser_et_corriger(c, etab, poste):
    """Rejette une soumission fraîche (motif tiré de instance.json ->
    motifs_ko), suit le lien de correction reçu par mail et constate que le
    dossier repart dans le parcours normal (retour à Soumise, cf. store.etat)."""
    kw = {"base_url": BASE_URL} if BASE_URL else {}
    r = c.post("/", data={**saisie(etab, poste),
                          **{p["id"]: [f() for _ in range(p.get("max_fichiers", 1))] for p in config.pieces()}},
               content_type="multipart/form-data", **kw)
    assert "Demande envoyée" in r.text, r.text[:300]
    uid = max(i["id"] for i in store.tout())

    motif = config.instance()["motifs_ko"][0]
    c.post(f"/dossier/{uid}/rejeter",
           data={"motif": motif, "commentaire": "contrôle driver : refus"}, **kw)
    assert store.etat(store.lire(uid)) == "Rejetee", store.etat(store.lire(uid))

    # lien_dans exige deja le prefixe BASE_URL (instance.json -> url) : sans le
    # base_url passe ci-dessus, le mail sortirait en "http://localhost" (sans
    # port) et cette recherche echouerait -- verifie par sabotage.
    lien = lien_dans(dernier_mail("rejet"), "corriger")

    r = c.post(lien, data={**saisie(etab, poste),
                           **{p["id"]: [f() for _ in range(p.get("max_fichiers", 1))] for p in config.pieces()}},
               content_type="multipart/form-data")
    assert "Correction envoyée" in r.text, r.text[:300]
    assert c.get(lien).status_code == 410, "lien de correction encore vivant"
    assert store.etat(store.lire(uid)) == "Soumise", \
        "le dossier ne repart pas dans le parcours après correction"
    return uid


def verifier_recap(uids_attendus, nom_attendu):
    """recap.py (mail hebdo au cabinet comptable) doit lister les dossiers
    remis, salarié compris -- même vérification que test_parcours.py."""
    import recap
    seuil = recap.datetime.now(recap.timezone.utc) - recap.timedelta(days=7)
    remis = {i["id"] for i in recap.remis_depuis(seuil)}
    for uid in uids_attendus:
        assert uid in remis, f"{uid} absent du récap 7 jours"
    try:
        recap.main(7)
    except SystemExit as e:
        assert e.code == 0, "le mail recap_hebdo n'est pas parti"
    fichiers = sorted((config.DONNEES / "mails").glob("*-recap_hebdo.eml"))
    assert fichiers, "aucun mail recap_hebdo envoyé"
    corps = "\n".join(
        email.message_from_bytes(f.read_bytes()).get_payload(decode=True).decode("utf-8")
        for f in fichiers)
    assert nom_attendu.upper() in corps, f"« {nom_attendu} » absent du récap :\n{corps}"


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": SPEC["password"]})
    assert c.get("/suivi").status_code == 200, "login RH échoué"

    uids = [couloir(c, SPEC["etab_genere"], "genere", SPEC["poste"], SPEC["attendus"])]
    if SPEC.get("etab_depose"):
        uids.append(couloir(c, SPEC["etab_depose"], "depose", SPEC["poste"], []))

    refuser_et_corriger(c, SPEC["etab_genere"], SPEC["poste"])
    verifier_recap(uids, "Nkemba")

    inst = config.instance()
    print(f"PARCOURS OK\t{inst['client']}\t{inst['secret'][:8]}\t{config.DONNEES}")


if __name__ == "__main__":
    main()
