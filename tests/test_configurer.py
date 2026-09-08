"""configurer.py : bilan lisible, assistant qui écrit, comptes.

    python tests/test_configurer.py

Tout passe par un sous-processus SANS PYTHONIOENCODING, sortie dans un pipe :
c'est le piège cp1252 de la vérification B (messages accentués de verifier()
qui faisaient planter le print de l'appelant), rejoué à chaque exécution.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RACINE = Path(__file__).resolve().parent.parent
BASE = Path(tempfile.mkdtemp(prefix="contratgen-configurer-"))
CONF = BASE / "config"
(CONF / "contrats").mkdir(parents=True)
sys.path.insert(0, str(RACINE))

from werkzeug.security import generate_password_hash, check_password_hash  # noqa: E402

INSTANCE = {
    "config_version": 1, "client": "ACME", "secret": "a" * 64, "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "rh@acme.example",
              "rh": ["rh@acme.example"], "dpae": None, "recap": None,
              "comptable_defaut": "c@acme.example"},
    "utilisateurs": {"rh": {"mdp_hash": generate_password_hash("pas-demo")}},
    "motifs_ko": ["Autre"], "templates": {"Manager": "contrat.txt"},
}
FORMULAIRE = {"roles": {"nom": "nom"}, "champs": [
    {"id": "etablissement", "libelle": "Établissement", "type": "etablissement", "requis": True},
    {"id": "email_demandeur", "libelle": "Votre email", "type": "email", "requis": True},
    {"id": "nom", "libelle": "Nom", "type": "texte", "requis": True, "placeholder": "Nom"},
    {"id": "poste", "libelle": "Poste", "type": "choix", "requis": True,
     "options": ["Manager"], "placeholder": "Poste"}]}
SOCIETES = [{"nom": "ACME", "siren": "111 111 111", "comptable_email": "c@acme.example",
             "mentions": {"RaisonSociale": "ACME SAS"},
             "etablissements": [{"nom": "Siège", "siret": "111 111 111 00011",
                                 "mentions": {"AdresseEtablissement": "1 rue X"}}]}]


def ecrire(instance=None, societes=None):
    (CONF / "instance.json").write_text(
        json.dumps(instance or INSTANCE, ensure_ascii=False), encoding="utf-8")
    (CONF / "formulaire.json").write_text(json.dumps(FORMULAIRE), encoding="utf-8")
    (CONF / "societes.json").write_text(
        json.dumps(SOCIETES if societes is None else societes), encoding="utf-8")
    (CONF / "contrats" / "contrat.txt").write_text(
        "Bonjour {{Nom}}, poste {{Poste}}.", encoding="utf-8")
    (CONF / "PLACEHOLDERS.md").unlink(missing_ok=True)


def lancer(*args, entree=""):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env.update(CONFIG_DIR=str(CONF), DONNEES=str(BASE / "data"))
    p = subprocess.run([sys.executable, str(RACINE / "configurer.py"), *args],
                       input=entree.encode("utf-8"), capture_output=True, env=env)
    sortie = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert "Traceback" not in sortie, sortie
    return p.returncode, sortie


def instance():
    return json.loads((CONF / "instance.json").read_text(encoding="utf-8"))


def test_bilan_config_saine():
    ecrire()
    code, sortie = lancer()
    assert code == 0 and sortie.rstrip().endswith("OK"), sortie
    assert (CONF / "PLACEHOLDERS.md").exists()
    assert "Couverture de « ACME »" in sortie, "rapport doctor absent"


def test_bilan_nomme_le_fichier_a_corriger():
    ecrire({**INSTANCE, "mails": {**INSTANCE["mails"], "rh": []}})
    code, sortie = lancer()
    assert code == 1 and "! mails  [config/instance.json]" in sortie, sortie
    assert "KO" in sortie and not (CONF / "PLACEHOLDERS.md").exists()


def test_assistant_renseigne_les_mails():
    casse = {**INSTANCE, "mails": {**INSTANCE["mails"], "rh": [], "expediteur": ""}}
    ecrire(casse)
    code, sortie = lancer("--assister", entree="\n\n")      # tout passé : rien d'écrit
    assert code == 1 and instance()["mails"] == casse["mails"], sortie
    code, sortie = lancer("--assister", entree="a@x.example, b@x.example\nrh@x.example\n")
    assert code == 0, sortie
    assert instance()["mails"]["rh"] == ["a@x.example", "b@x.example"]
    assert instance()["mails"]["expediteur"] == "rh@x.example"


def test_compte_ajoute_un_utilisateur():
    ecrire()
    code, sortie = lancer("compte", "marc", entree="s3cret\nautre\n")
    assert code == 1 and "marc" not in instance()["utilisateurs"], sortie
    code, sortie = lancer("compte", "marc", entree="s3cret\ns3cret\n")
    assert code == 0, sortie
    users = instance()["utilisateurs"]
    assert check_password_hash(users["marc"]["mdp_hash"], "s3cret")
    assert users["rh"] == INSTANCE["utilisateurs"]["rh"], "compte rh écrasé"


def test_assistant_cree_la_premiere_societe():
    ecrire(societes=[])
    reponses = ["Toitures du Nord", "494 118 220", "cab@tdn.example", "TDN SAS",
                "1 rue du Nord, Lille", "Chantier Nord", "494 118 220 00015",
                "chef@tdn.example", "2 rue du Nord", "", "o",
                "Dépôt Roubaix", "494 118 220 00023", "", "3 rue de Roubaix", "n", ""]
    code, sortie = lancer("--assister", entree="\n".join(reponses) + "\n")
    assert code == 0, sortie
    soc = json.loads((CONF / "societes.json").read_text(encoding="utf-8"))
    assert [e["nom"] for e in soc[0]["etablissements"]] == ["Chantier Nord", "Dépôt Roubaix"]
    assert soc[0]["etablissements"][1]["contrat"] == "depose"
    assert soc[0]["etablissements"][0]["manager_email"] == "chef@tdn.example"
    assert "contrat" not in soc[0]["etablissements"][0]


if __name__ == "__main__":
    import shutil
    try:
        for nom, fn in sorted(globals().items()):
            if nom.startswith("test_"):
                fn()
                print(f"  ✓ {nom}")
        print("\nconfigurer.py : bilan lisible sous cp1252, assistant, comptes.")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
