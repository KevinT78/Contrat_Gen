"""Driver : joue le parcours complet dans UNE copie du dépôt déjà installée.

    python tests/_client_parcours.py <racine-copie> <spec.json>

Lancé en sous-processus par tests/test_clients.py, une fois par client factice.
Sort 0 si le parcours passe, imprime « PARCOURS OK <client> <secret8> <donnees> ».
"""
import html
import io
import json
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
    return vals


def couloir(c, etab, mode, poste, attendus):
    r = c.post("/", data={**saisie(etab, poste),
                          **{p["id"]: f() for p in config.pieces()}},
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
    if config.instance().get("fiche_salarie"):
        assert store.fichiers_role(item, "pieces", "fiche-salarie"), "fiche absente"

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


def main():
    assert not config.verifier(), f"config non servable : {config.verifier()}"
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": SPEC["password"]})
    assert c.get("/suivi").status_code == 200, "login RH échoué"

    couloir(c, SPEC["etab_genere"], "genere", SPEC["poste"], SPEC["attendus"])
    if SPEC.get("etab_depose"):
        couloir(c, SPEC["etab_depose"], "depose", SPEC["poste"], [])

    inst = config.instance()
    print(f"PARCOURS OK\t{inst['client']}\t{inst['secret'][:8]}\t{config.DONNEES}")


if __name__ == "__main__":
    main()
