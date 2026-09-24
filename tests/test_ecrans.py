"""Chaque écran rend sans erreur, dans chacun des états du dossier.

    python tests/test_ecrans.py

La suite existante vérifie les transitions mais n'ouvre jamais GET /dossier :
une faute de template (fil d'étapes, badge, journal) passait inaperçue.
Écrit dans un dossier temporaire, ne touche jamais data/.
"""
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-ecrans-")

import config          # noqa: E402
import store           # noqa: E402
from app import app    # noqa: E402

app.config["PROPAGATE_EXCEPTIONS"] = True
for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

ETAB = config.etablissements()[0][0]
# Un établissement « déposé » : la validation n'y génère pas le contrat, l'écran
# ATraiter reste donc un vrai passage (les autres établissements sautent direct
# à ContratPret depuis la validation).
ETAB_DEPOSE = next(cle for cle, _ in config.etablissements()
                   if config.mode_contrat(cle) == "depose")


def piece():
    return (BytesIO(b"%PDF-1.4\n%%EOF"), "p.pdf")


def saisie(etab=ETAB):
    return {"etablissement": etab, "email_demandeur": "manager@example.com",
            "nom_naissance": "Martin", "nom_usage": "", "prenom": "Camille",
            "date_naissance": "1998-04-11", "lieu_naissance": "Lille",
            "nationalite": "Française", "num_secu": "2 98 04 59 350 042 21",
            "adresse": "14 rue Nationale, 59000 Lille", "poste": "Manager",
            "type_contrat": "CDI", "date_debut": "2026-10-01",
            "temps_partiel": "Non", "heures_hebdo": "35", "salaire": "2450"}


def ecran(c, url, attendu=200):
    r = c.get(url)
    assert r.status_code == attendu, f"{url} -> {r.status_code}"
    assert "{{" not in r.text, f"{url} : placeholder Jinja non rendu"
    return r.text


def soumettre(c, etab=ETAB):
    c.post("/", data={**saisie(etab), "identite": [piece(), piece()],
                      "carte_vitale": piece(), "rib": piece()},
           content_type="multipart/form-data")
    return max(i["id"] for i in store.tout())


MANQUE = "signé manquant"


def ligne(texte, uid):
    """La ligne <tr> du dossier `uid` dans un tableau de /suivi ou /salaries."""
    debut = texte.rindex("<tr", 0, texte.index(f"/dossier/{uid}"))
    return texte[debut:texte.index("</tr>", debut)]


def etapes(texte):
    """La frise d'étapes (<ul class="etapes">) d'un écran dossier."""
    frise = texte[texte.index('class="etapes"'):]
    return frise[:frise.index("</ul>")]


def main():
    anonyme = app.test_client()
    ecran(anonyme, "/")                         # formulaire candidat
    ecran(anonyme, "/login")
    assert anonyme.get("/suivi").status_code == 302, "suivi ouvert sans session"

    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    ecran(c, "/suivi")
    ecran(c, "/salaries")
    ecran(c, f"/salaries?etablissement={ETAB}")
    ecran(c, f"/suivi?etablissement={ETAB}")
    vide = ecran(c, "/suivi?etat=NExistePas")   # 200, pas 500
    assert 'class="nom"' not in vide, "un ?etat= inconnu doit rendre zéro ligne"

    vus = set()
    uid = soumettre(c)
    fichier = {"content_type": "multipart/form-data"}

    def voir():
        e = store.etat(store.lire(uid))
        vus.add(e)
        texte = ecran(c, f"/dossier/{uid}")
        assert libelle(e) in texte, f"état « {e} » absent de l'écran"
        return texte

    def libelle(e):
        from app import LIBELLES_ETAT
        return LIBELLES_ETAT[e]

    voir()                                                          # Soumise
    c.post(f"/dossier/{uid}/rejeter", data={"motif": "Pièce illisible ou manquante",
                                            "commentaire": "RIB flou"})
    voir()                                                          # Rejetee

    uid = soumettre(c, ETAB_DEPOSE)
    voir()
    c.post(f"/dossier/{uid}/valider")
    voir()                                                          # ATraiter (étab. déposé)
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()}, **fichier)
    voir()                                                          # ContratPret
    assert MANQUE not in ligne(ecran(c, "/suivi"), uid), "badge « signé manquant » dans le suivi"
    c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()}, **fichier)
    voir()                                                          # DpaeFaite
    r = c.post(f"/dossier/{uid}/remettre")
    assert r.headers["Location"].endswith("/suivi"), "la remise doit atterrir sur /suivi"
    atterri = ecran(c, "/suivi")                                    # consomme les flashs
    assert "popup-validation" in atterri and "mail hebdomadaire" in atterri, \
        "la remise doit afficher la pop-up sur /suivi"
    texte = voir()                                                  # RemisComptable

    # Un dossier au bout du parcours quitte /suivi (demandes en cours) et
    # apparaît sur /salaries (procédure terminée).
    lien = f"/dossier/{uid}"
    assert lien not in ecran(c, "/suivi"), "RemisComptable devrait avoir quitté /suivi"
    assert lien in ecran(c, "/salaries"), "RemisComptable absent de /salaries"
    assert MANQUE in ligne(ecran(c, "/salaries"), uid), "badge « signé manquant » absent de Salariés"

    import re
    menu = re.search(r'<a href="[^"]*"\s*(aria-current=page)?>Suivi d\'embauche</a>\s*'
                     r'<a href="[^"]*"\s*(aria-current=page)?>Salariés</a>', texte)
    assert menu and not menu.group(1) and menu.group(2), \
        "RemisComptable : le menu devrait surligner « Salariés », pas « Suivi d'embauche »"
    assert "Abandonner le dossier" not in texte, \
        "RemisComptable ne devrait plus proposer d'abandon"
    assert f'action="/dossier/{uid}/archiver"' in texte, \
        "RemisComptable devrait proposer le formulaire d'archivage"
    assert 'class="etapes"' not in texte, \
        "RemisComptable ne devrait plus afficher la frise du recrutement"
    assert (texte.index("Documents produits") < texte.index("Lien comptable")
            < texte.index("Départ du salarié") < texte.index("<h2>Journal</h2>")), \
        "RemisComptable : lien puis départ doivent suivre la fiche salarié, avant le journal"
    c.post(f"/dossier/{uid}/abandonner")
    assert store.etat(store.lire(uid)) == "RemisComptable", \
        "RemisComptable : un POST direct sur /abandonner devrait être refusé"
    autre = config.etablissements()[1][0]
    assert lien not in ecran(c, f"/salaries?etablissement={autre}"), \
        "filtre établissement inopérant sur /salaries"

    item = store.lire(uid)
    jeton = store.signer("lot_comptable", uid, item.get("lien_comptable_epoch", 0))
    ecran(app.test_client(), f"/lot/{jeton}")                       # écran comptable

    # Yousign aux mêmes endroits que le dépôt manuel : aussi sur la fiche salarié
    sig = config.instance().setdefault("signature", {})
    sig["mode"] = "yousign"
    try:
        assert f'action="/dossier/{uid}/signature-envoyer"' in voir(), \
            "Envoyer à la signature absent en RemisComptable"
    finally:
        sig["mode"] = "manuel"

    c.post(f"/dossier/{uid}/archiver", data={"date_sortie": "2026-09-30"})
    voir()                                                          # Parti
    n = len(store.lire(uid)["journal"])
    r = c.post(f"/dossier/{uid}/signature-envoyer", follow_redirects=True)
    assert "Pas de contrat signé à déposer" in r.text, "envoi en signature accepté en Parti"
    assert len(store.lire(uid)["journal"]) == n
    assert MANQUE not in ecran(c, "/salaries?anciens=1"), "badge chez les anciens salariés"
    assert lien not in ecran(c, "/salaries"), "Parti encore dans « En poste »"
    assert lien in ecran(c, "/salaries?anciens=1"), "Parti absent de « Anciens salariés »"

    uid = soumettre(c)
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/abandonner")
    voir()                                                          # Abandonnee

    # Dossier hérité, écrit à ContratSigne avant la suppression de l'étape :
    # la frise le place sur « Contrat prêt », la DPAE reste possible.
    ancien = soumettre(c, ETAB_DEPOSE)
    c.post(f"/dossier/{ancien}/valider")
    c.post(f"/dossier/{ancien}/contrat-depose", data={"contrat": piece()}, **fichier)
    item = store.lire(ancien)
    item["journal"].append({"de": "ContratPret", "vers": "ContratSigne",
                            "le": store.maintenant(), "par": "rh"})
    store._ecrire(Path(item["_dir"]), item)
    assert store.etat(store.lire(ancien)) == "ContratSigne"
    texte = ecran(c, f"/dossier/{ancien}")
    frise = etapes(texte)
    assert "Contrat signé" not in frise, frise
    ici = frise[frise.index('class="ici"'):]
    assert ici[:ici.index("</li>")].count("Contrat prêt") == 1, "état hérité hors frise"
    assert f'action="/dossier/{ancien}/dpae-faite"' in texte, "DPAE absente sur un dossier hérité"
    c.post(f"/dossier/{ancien}/dpae-faite", data={"accuse": piece()}, **fichier)
    assert store.etat(store.lire(ancien)) == "DpaeFaite", "DPAE refusée sur un dossier hérité"

    # ContratPret : pas d'étape « Contrat signé » dans la frise, formulaire
    # DPAE direct ; le signé ne se dépose que depuis la vue Salarié.
    uid = soumettre(c, ETAB_DEPOSE)
    c.post(f"/dossier/{uid}/valider")
    c.post(f"/dossier/{uid}/contrat-depose", data={"contrat": piece()}, **fichier)
    depot = f'action="/dossier/{uid}/contrat-signe"'
    envoi = f'action="/dossier/{uid}/signature-envoyer"'
    sig["mode"] = "yousign"
    try:
        texte = voir()                                              # ContratPret
        frise = etapes(texte)
        assert "Contrat signé" not in frise, frise
        assert f'action="/dossier/{uid}/dpae-faite"' in texte, "formulaire DPAE absent"
        assert depot not in texte and envoi not in texte, "dépôt du signé avant la remise"
        assert MANQUE not in ligne(ecran(c, "/suivi"), uid), "badge dans le Suivi"
        c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()}, **fichier)
        assert not store.fichiers_role(store.lire(uid), "contrat", "contrat-signe"), \
            "dépôt du signé accepté avant la remise"
        c.post(f"/dossier/{uid}/dpae-faite", data={"accuse": piece()}, **fichier)
        texte = voir()                                              # DpaeFaite
        assert depot not in texte and envoi not in texte, "dépôt du signé en DPAE faite"
        # remis sans signé : badge dans Salariés, bloc en haut de la fiche
        c.post(f"/dossier/{uid}/remettre")
        assert MANQUE in ligne(ecran(c, "/salaries"), uid), "badge absent de Salariés"
        texte = voir()
        assert depot in texte and envoi in texte, "dépôt / Yousign absents de la fiche salarié"
        assert texte.index(depot) < texte.index("Informations saisies"), "bloc signé pas en haut"
        verif = f'action="/dossier/{uid}/signature-verifier"'
        assert verif not in texte, "Vérifier sans envoi à la signature"
        store.noter(uid, type="signature_envoyee", par="rh", procedure="p-test")
        assert verif in voir(), "Vérifier la signature absent après l'envoi"
        c.post(f"/dossier/{uid}/contrat-signe", data={"signe": piece()}, **fichier)
        texte = voir()
        assert depot not in texte and envoi not in texte, "dépôt encore proposé après le signé"
        assert "Déposé le" in texte, "date de dépôt du signé absente"
        assert texte.index("Déposé le") > texte.index("Lien comptable"), "« Déposé le » pas sous Lien comptable"
        assert MANQUE not in ligne(ecran(c, "/salaries"), uid), "badge encore là dans Salariés"
    finally:
        sig["mode"] = "manuel"

    manque = set(store.ETATS) - vus
    assert not manque, f"états jamais rendus : {sorted(manque)}"
    print(f"ÉCRANS OK — {len(vus)} états rendus, {config.DONNEES}")


if __name__ == "__main__":
    main()
