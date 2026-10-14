"""configurer.py : bilan lisible, assistant qui écrit, comptes.

    python tests/test_configurer.py

Tout passe par un sous-processus SANS PYTHONIOENCODING, sortie dans un pipe :
c'est le piège cp1252 de la vérification B (messages accentués de verifier()
qui faisaient planter le print de l'appelant), rejoué à chaque exécution.
"""
import json
import os
import shutil
import socket
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
from _outils import base_mongo  # noqa: E402
base_mongo("configurer")

from werkzeug.security import check_password_hash  # noqa: E402
from _outils import compte, societes as poser_societes  # noqa: E402
compte("rh", "pas-demo")      # le compte vit en base : lu par l'enfant via l'amorce

INSTANCE = {
    "config_version": 1, "client": "ACME", "secret": "a" * 64, "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "rh@acme.example",
              "rh": ["rh@acme.example"], "dpae": None, "recap": None,
              "comptable_defaut": "c@acme.example"},
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
    poser_societes(SOCIETES if societes is None else societes)   # base + amorce de l'enfant
    (CONF / "contrats" / "contrat.txt").write_text(
        "Bonjour {{Nom}}, poste {{Poste}}.", encoding="utf-8")
    (CONF / "PLACEHOLDERS.md").unlink(missing_ok=True)


def lancer(*args, entree="", sans=()):
    # MAILS_MODE aussi : le .bat MailHog le pose, il surclasserait le mode des fixtures
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "MAILS_MODE", *sans)}
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


def test_bilan_n_invente_pas_de_chemin_pour_un_fichier_hors_config():
    """Le modèle générique de fiche salarié est versé AVEC LE CODE, hors de
    config/. Son sujet tombait sur le défaut « contrats/<sujet> » : l'installateur
    lisait « [config/contrats/fiche salarié] » — un dossier où ce fichier n'a
    jamais à être — juste sous un message qui dit de copier « modeles/ »."""
    import io
    import contextlib
    import configurer

    # Le préfixe fait partie de la VALEUR : tout ne vit pas sous config/.
    assert configurer.fichier("fiche salarié") == "modeles/fiche_salarie.docx"
    assert configurer.fichier("mails") == "config/instance.json"
    assert configurer.fichier("établissements").startswith("en base")
    assert configurer.fichier("grille — barème « Equipier »") == "config/grille.json"
    # un sujet inconnu reste un modèle à déposer chez le client
    assert configurer.fichier("Manager.docx") == "config/contrats/Manager.docx"

    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        configurer.afficher({"fiche salarié": ["fichier absent : ...\\modeles\\f.docx"],
                             "mails": ["« rh » vide"]})
    sortie = tampon.getvalue()
    assert "config/contrats/fiche salarié" not in sortie, sortie
    # chaque ligne garde son crochet : alignement conservé
    assert "! fiche salarié  [modeles/fiche_salarie.docx]" in sortie, sortie
    assert "! mails  [config/instance.json]" in sortie, sortie


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
    assert code == 1 and "diffèrent" in sortie, sortie
    # En process : la base mongomock d'un enfant n'est pas visible d'ici.
    import configurer
    import store
    configurer._mot_de_passe = lambda question: "s3cret"
    assert configurer.compte("marc") == 0
    comptes = store.db().comptes
    assert check_password_hash(comptes.find_one({"_id": "marc"})["mdp_hash"], "s3cret")
    assert comptes.find_one({"_id": "rh"}), "compte rh écrasé"


def test_bilan_sans_base_est_un_manque_pas_une_trace():
    """Sans MONGO_*, les comptes sont illisibles : le bilan le dit (lancer()
    refuse toute Traceback) au lieu de planter avant d'avoir rien affiché."""
    ecrire()
    code, sortie = lancer(sans=("MONGO_URI", "MONGO_DB"))
    assert code == 1 and "base mongo non configurée" in sortie, sortie
    assert "configurer.py compte" in sortie, sortie
    code, sortie = lancer("compte", "rh", entree="s3cret\ns3cret\n",
                          sans=("MONGO_URI", "MONGO_DB"))
    assert code == 1 and "ÉCHEC" in sortie, sortie


def test_bilan_refuse_un_id_de_champ_avec_point():
    """store ecrit `champs.<id>` : « a.b » y creerait un sous-document."""
    ecrire()
    f = json.loads(json.dumps(FORMULAIRE))
    f["champs"].append({"id": "a.b", "libelle": "X", "type": "texte"})
    (CONF / "formulaire.json").write_text(json.dumps(f), encoding="utf-8")
    code, sortie = lancer()
    assert code == 1 and "« a.b »" in sortie and "[config/formulaire.json]" in sortie, sortie


def test_bilan_refuse_un_chemin_de_drive_absent():
    """Le mode « dossier » est vérifié au démarrage, pas à la 1re soumission :
    un dossier de sync pas encore répliqué doit sortir un KO qui nomme
    instance.json et dit pourquoi."""
    ecrire({**INSTANCE, "stockage": {"mode": "dossier",
                                     "chemin": str(BASE / "drive-absent")}})
    code, sortie = lancer()
    assert code == 1 and "! stockage  [config/instance.json]" in sortie, sortie
    assert "drive-absent" in sortie and "n'est pas un dossier" in sortie, sortie

    # Les formes que la validation doit REFUSER, pas seulement le cas valide.
    for bloc, attendu in (({"mode": "cloud"}, "inconnu"),
                          ({"mode": "dossier"}, "sans « chemin »"),
                          ({"mode": "dossier", "chemin": "  "}, "sans « chemin »"),
                          ("local", "attend un objet")):
        ecrire({**INSTANCE, "stockage": bloc})
        code, sortie = lancer()
        assert code == 1 and attendu in sortie, f"{bloc} : {sortie}"

    # Et le cas valide passe : dossier existant et accessible en écriture.
    drive = BASE / "drive"
    drive.mkdir(exist_ok=True)
    ecrire({**INSTANCE, "stockage": {"mode": "dossier", "chemin": str(drive)}})
    code, sortie = lancer()
    assert code == 0, sortie
    assert not list(drive.iterdir()), "la sonde d'écriture a laissé un fichier"


def test_assistant_pose_le_bloc_stockage():
    drive = BASE / "drive-assistant"
    drive.mkdir(exist_ok=True)
    ecrire({**INSTANCE, "stockage": {"mode": "dossier", "chemin": str(BASE / "nulle-part")}})
    code, sortie = lancer("--assister", entree=f"2\n{drive}\n")
    assert code == 0, sortie
    assert instance()["stockage"] == {"mode": "dossier", "chemin": str(drive)}

    ecrire({**INSTANCE, "stockage": {"mode": "dossier", "chemin": str(BASE / "nulle-part")}})
    code, sortie = lancer("--assister", entree="1\n")
    assert code == 0, sortie
    assert instance()["stockage"] == {"mode": "local"}


def test_assistant_cree_la_premiere_societe():
    ecrire(societes=[])
    reponses = ["Toitures du Nord", "494 118 220", "cab@tdn.example", "TDN SAS",
                "1 rue du Nord, Lille", "Chantier Nord", "494 118 220 00015",
                "chef@tdn.example", "2 rue du Nord", "", "o",
                "Dépôt Roubaix", "494 118 220 00023", "", "3 rue de Roubaix", "n", ""]
    code, sortie = lancer("--assister", entree="\n".join(reponses) + "\n")
    assert code == 0, sortie          # l'enfant a relu sa société en base : bilan OK
    # Ce qui est écrit se lit en process : la base de l'enfant n'est pas visible d'ici.
    import config
    _reparer_en_process(reponses)
    soc = config.societes()
    assert [e["nom"] for e in soc[0]["etablissements"]] == ["Chantier Nord", "Dépôt Roubaix"]
    assert soc[0]["etablissements"][1]["contrat"] == "depose"
    assert soc[0]["etablissements"][0]["manager_email"] == "chef@tdn.example"
    assert "contrat" not in soc[0]["etablissements"][0]


def test_assistant_rattache_a_une_societe_existante():
    """Société déjà en base : ses établissements s'y ajoutent, sa fiche ne bouge
    pas, et un doublon est ignoré sans perdre ceux qui suivent."""
    import config
    ecrire()                                          # ACME / Siège
    sortie = _reparer_en_process([
        "ACME", "999 999 999", "autre@x.example", "AUTRE SAS", "ailleurs",
        "Siège", "1", "", "1 rue X", "", "o",
        "Annexe", "111 111 111 00029", "", "2 rue Y", "", "n"])
    acme = config.societes()[0]
    assert [e["nom"] for e in acme["etablissements"]] == ["Siège", "Annexe"], acme
    assert acme["siren"] == "111 111 111" and acme["mentions"] == SOCIETES[0]["mentions"]
    assert "existe déjà" in sortie and "« Siège » ignoré" in sortie, sortie


def _reparer_en_process(reponses):
    """_reparer_etablissements avec ces réponses ; rend ce qu'il a affiché."""
    import contextlib
    import io
    import configurer
    questions = iter(reponses)
    vrai = configurer._demander
    configurer._demander = lambda q: next(questions)
    tampon = io.StringIO()
    try:
        with contextlib.redirect_stdout(tampon):
            configurer._reparer_etablissements([])
    finally:
        configurer._demander = vrai
    return tampon.getvalue()


PROD = {**INSTANCE, "url": "https://embauche.acme.example",
        "mails": {**INSTANCE["mails"], "mode": "smtp", "hote": "127.0.0.1"},
        "conservation": {"jours": 1095, "jours_candidature": 730,
                         "apres": ["RemisComptable", "Rejetee", "Abandonnee"]}}
SOCIETES_PROD = [{**SOCIETES[0], "etablissements": [
    {**SOCIETES[0]["etablissements"][0], "manager_email": "chef@acme.example"}]}]


def test_bilan_avertit_sans_bloquer():
    """Ce que verifier() accepte parce que c'est legitime en demo, mais faux
    sur une instance client : averti, jamais bloquant (code de sortie inchange).
    Les 5 pieges mesures le 2026-09-14, bilan OK sur chacun."""
    ecrire(PROD, SOCIETES_PROD)
    code, sortie = lancer()
    assert code == 0 and "⚠" not in sortie, sortie

    sans_cabinet = [{**SOCIETES_PROD[0], "comptable_email": ""}]
    for instance_, societes, attendu in (
            ({**PROD, "mails": {**PROD["mails"], "mode": "console"}}, SOCIETES_PROD, "console"),
            (PROD, SOCIETES, "manager_email"),
            ({**PROD, "mails": {**PROD["mails"], "comptable_defaut": ""}}, sans_cabinet,
             "cabinet"),
            ({**PROD, "url": "http://embauche.acme.example"}, SOCIETES_PROD, "https"),
            ({k: v for k, v in PROD.items() if k != "conservation"}, SOCIETES_PROD,
             "conservation")):
        ecrire(instance_, societes)
        code, sortie = lancer()
        avert = [l for l in sortie.splitlines() if "⚠" in l]
        assert code == 0, sortie
        assert len(avert) == 1 and attendu in avert[0], f"{attendu} : {avert}"


def _faux_smtp():
    """Relais SMTP minimal sur un port libre -> (port, liste des messages recus)."""
    import threading
    recus = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def servir():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        dire = lambda l: (f.write(l.encode() + b"\r\n"), f.flush())
        dire("220 faux")
        while (l := f.readline().decode().strip()):
            v = l.split(" ")[0].upper()
            if v == "DATA":
                dire("354 go")
                corps = []
                while (x := f.readline().decode()).strip() != ".":
                    corps.append(x)
                recus.append("".join(corps))
                dire("250 ok")
            elif v == "QUIT":
                dire("221 bye")
                break
            else:
                dire("250 ok")
        conn.close()
        srv.close()

    threading.Thread(target=servir, daemon=True).start()
    return srv.getsockname()[1], recus


def test_mail_envoie_en_smtp_meme_en_mode_console():
    """`configurer.py mail` sert a tester le relais AVANT de passer en smtp :
    il doit envoyer pour de vrai, jamais ecrire un .eml en mode console."""
    port, recus = _faux_smtp()
    ecrire({**INSTANCE, "mails": {**INSTANCE["mails"], "hote": "127.0.0.1", "port": port}})
    (CONF / "mails").mkdir(exist_ok=True)                 # toute instance reçoit les 4
    shutil.copy(RACINE / "config.exemple" / "mails" / "nouvelle_soumission.txt",
                CONF / "mails")
    code, sortie = lancer("mail", "vous@acme.example")
    assert code == 0, sortie
    assert len(recus) == 1 and "vous@acme.example" in recus[0], (recus, sortie)
    assert "[MAIL" not in sortie, "ecrit en console au lieu d'envoyer"

    with socket.socket() as s:                        # port ferme -> echec lisible
        s.bind(("127.0.0.1", 0))
        ferme = s.getsockname()[1]
    ecrire({**INSTANCE, "mails": {**INSTANCE["mails"], "hote": "127.0.0.1", "port": ferme}})
    code, sortie = lancer("mail", "vous@acme.example")
    assert code == 1 and "ÉCHEC" in sortie, sortie

    # relais non renseigne, ou bloc mails absent : echec lisible, pas de trace
    for inst in (INSTANCE, {k: v for k, v in INSTANCE.items() if k != "mails"}):
        ecrire(inst)
        code, sortie = lancer("mail", "vous@acme.example")      # lancer() refuse « Traceback »
        assert code == 1 and "mails.hote vide" in sortie, sortie


if __name__ == "__main__":
    try:
        for nom, fn in sorted(globals().items()):
            if nom.startswith("test_"):
                fn()
                print(f"  ✓ {nom}")
        print("\nconfigurer.py : bilan lisible sous cp1252, assistant, comptes.")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
