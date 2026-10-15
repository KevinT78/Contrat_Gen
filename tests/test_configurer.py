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
    "config_version": 1, "client": "ACME", "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "expediteur": "rh@acme.example",
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
    (CONF / "mails").mkdir(exist_ok=True)
    for m in ("nouvelle_soumission", "demande_validee", "rejet", "rappel_dpae", "recap_hebdo"):
        (CONF / "mails" / f"{m}.txt").write_text("Objet: x\n\nBonjour", encoding="utf-8")


def lancer(*args, entree="", sans=()):
    # MAILS_MODE aussi : le .bat MailHog le pose, il surclasserait le mode des fixtures
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "MAILS_MODE", *sans)}
    env.update(CONFIG_DIR=str(CONF))
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
    """Sans MONGO_*, la base est illisible : le bilan le dit une fois (lancer()
    refuse toute Traceback) au lieu de planter avant d'avoir rien affiché."""
    ecrire()
    code, sortie = lancer(sans=("MONGO_URI", "MONGO_DB"))
    assert code == 1 and "MONGO_URI absent" in sortie and "MONGO_DB absent" in sortie, sortie
    assert "comptes" not in sortie, "le même manque redit par chaque check qui lit la base"
    # URI au mauvais schéma : le manque, pas l'InvalidURI de pymongo (lancer()
    # refuse toute Traceback), et jamais le mot de passe qu'elle porte.
    os.environ["MONGO_URI"], vraie = "postgres://u:mdp-uri@h/x", os.environ["MONGO_URI"]
    try:
        code, sortie = lancer()
    finally:
        os.environ["MONGO_URI"] = vraie
    assert code == 1 and "doit commencer par mongodb://" in sortie, sortie
    assert "mdp-uri" not in sortie, sortie
    code, sortie = lancer("compte", "rh", entree="s3cret\ns3cret\n",
                          sans=("MONGO_URI", "MONGO_DB"))
    assert code == 1 and "ÉCHEC" in sortie, sortie


def test_option_env_charge_le_fichier_de_l_instance():
    """--env <fichier> : coupe au premier « = », BOM du Bloc-notes toléré,
    commentaire ignoré, l'environnement déjà posé l'emporte, et l'option sort
    d'argv pour que le script lise ses propres arguments."""
    import config
    f = BASE / "essai.env"
    f.write_text("A_ENV_TEST=x=y=\nB_ENV_TEST=fichier\n# C_ENV_TEST=non\n",
                 encoding="utf-8-sig")
    os.environ["B_ENV_TEST"] = "deja-la"
    argv = ["configurer.py", "--env", str(f), "--assister"]
    try:
        config._charger_env(argv)
        assert argv == ["configurer.py", "--assister"], argv
        assert os.environ["A_ENV_TEST"] == "x=y=", os.environ["A_ENV_TEST"]
        assert os.environ["B_ENV_TEST"] == "deja-la", "le fichier a écrasé l'environnement"
        assert "C_ENV_TEST" not in os.environ
    finally:
        for k in ("A_ENV_TEST", "B_ENV_TEST"):
            os.environ.pop(k, None)
    try:
        config._charger_env(["x", "--env", str(BASE / "absent.env")])
        raise AssertionError("fichier absent accepté")
    except SystemExit as e:
        assert "absent.env" in str(e), e

    # Bout à bout : les secrets ne viennent QUE du fichier, et le bilan les voit.
    ecrire()
    f.write_text("".join(f"{k}={os.environ[k]}\n" for k in
                         ("SECRET_KEY", "MONGO_URI", "MONGO_DB", "BASE_EN_MEMOIRE")),
                 encoding="utf-8")
    code, sortie = lancer("--env", str(f),
                          sans=("SECRET_KEY", "MONGO_URI", "MONGO_DB", "BASE_EN_MEMOIRE"))
    assert "absent de l'environnement" not in sortie, sortie
    code, sortie = lancer(sans=("SECRET_KEY", "MONGO_URI", "MONGO_DB", "BASE_EN_MEMOIRE"))
    assert "SECRET_KEY absent" in sortie, "contrôle : sans --env, le manque doit sortir"


def test_option_env_cas_limites():
    """Chaque forme de fichier qu'un installateur peut produire à la main :
    soit une valeur exacte, soit un refus qui dit quoi corriger, jamais une
    trace ni un fichier ignoré en silence."""
    import config
    f = BASE / "limite.env"
    lit = [  # (octets du fichier, valeur attendue de LIM, ou fragment du refus)
        (b"LIM=a=b=", "a=b="),                      # coupe au premier « = »
        (b"LIM= a b \r\n", "a b"),                 # espaces et CRLF retires
        (b'LIM="mot de passe"', "mot de passe"),    # guillemets englobants retires
        (b"LIM='x'", "x"),
        (b'LIM="x', '"x'),                          # guillemet non apparie : garde
        (b"LIM=", ""),
        (b"\xef\xbb\xbfLIM=bom", "bom"),            # BOM du Bloc-notes
        (b"# LIM=non\n\n  \nLIM=oui", "oui"),        # commentaire et lignes vides
        (b"LIM=\xe9", "refus:pas en UTF-8"),        # cp1252
        (b"=x", "refus:ligne 1"),                   # cle vide
        (b"LIM x", "refus:ligne 1"),                # pas de « = »
        (b"A=1\nLIM mdp-secret", "refus:ligne 2"),  # numero de ligne juste
    ]
    for octets, attendu in lit:
        f.write_bytes(octets)
        os.environ.pop("LIM", None)
        argv = ["prog", "--env", str(f)]
        try:
            config._charger_env(argv)
            obtenu = os.environ.get("LIM")
        except SystemExit as e:
            obtenu = f"refus:{e}"
            assert "mdp-secret" not in str(e), f"le refus affiche la ligne : {e}"
        finally:
            os.environ.pop("A", None)
        if attendu.startswith("refus:"):
            assert obtenu.startswith("refus:") and attendu[6:] in obtenu, (octets, obtenu)
        else:
            assert obtenu == attendu and argv == ["prog"], (octets, obtenu, argv)

    f.write_bytes(b"LIM=fichier")
    for argv, attendu in (
            (["p", f"--env={f}", "x"], "fichier"),                  # forme --env=
            (["p", "--env", str(f), "--env", str(f)], "refus:deux fois"),
            (["p", "--env"], "refus:attend le chemin"),
            (["p", "--env", str(BASE)], "refus:illisible")):        # un dossier
        os.environ.pop("LIM", None)
        try:
            config._charger_env(argv)
            obtenu = os.environ.get("LIM")
        except SystemExit as e:
            obtenu = f"refus:{e}"
        assert (obtenu.startswith("refus:") and attendu[6:] in obtenu
                if attendu.startswith("refus:") else obtenu == attendu), (argv, obtenu)
    # une variable posée VIDE ne masque pas le fichier ; posée non vide, elle gagne
    # et une valeur différente du fichier se signale, sans afficher l'une ni l'autre
    import contextlib
    import io
    for avant, attendu, averti in (("", "fichier", False), ("fichier", "fichier", False),
                                   ("posee", "posee", True)):
        os.environ["LIM"] = avant
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            config._charger_env(["p", "--env", str(f)])
        assert os.environ["LIM"] == attendu, (avant, os.environ["LIM"])
        assert ("LIM déjà posé" in err.getvalue()) == averti, (avant, err.getvalue())
        assert "posee" not in err.getvalue() and "=fichier" not in err.getvalue()
    os.environ.pop("LIM", None)


def test_verifier_mongo_cas_limites():
    """Ce qui ferait lever pymongo en pleine vérification, ou démarrer sur une
    base qu'on n'attend pas, sort en manque lisible."""
    import config
    bon = {"SECRET_KEY": "k" * 40, "MONGO_URI": "mongodb://hote:27017/",
           "MONGO_DB": "acme", "BASE_EN_MEMOIRE": ""}
    cas = [
        ({}, None),
        ({"MONGO_URI": "mongodb+srv://u:p@cluster.exemple/?retryWrites=true"}, None),
        ({"MONGO_URI": "mongomock://", "BASE_EN_MEMOIRE": "1"}, None),
        ({"MONGO_URI": "mongomock://", "BASE_EN_MEMOIRE": "0"}, "base en mémoire"),
        ({"MONGO_URI": "mongomock://"}, "base en mémoire"),
        ({"MONGO_URI": "MONGODB://hote/"}, "doit commencer"),
        ({"MONGO_URI": " mongodb://hote/"}, "doit commencer"),
        ({"MONGO_URI": "mongodb://"}, "sans adresse"),
        ({"MONGO_URI": "mongodb:///acme"}, "sans adresse"),
        ({"MONGO_URI": "mongodb+srv://u:p@/"}, "sans adresse"),
        ({"SECRET_KEY": "court"}, "trop courte"),
        ({"SECRET_KEY": " " * 40}, "trop courte"),
        ({"MONGO_DB": "a.b"}, "nom de base invalide"),
        ({"MONGO_DB": "a b"}, "nom de base invalide"),
        ({"MONGO_DB": "x" * 64}, "nom de base invalide"),
        ({"MONGO_DB": "x" * 63}, None),
    ]
    sauve = {k: os.environ.get(k) for k in bon}
    try:
        for change, attendu in cas:
            os.environ.update({**bon, **change})
            m = config._verifier_mongo()
            texte = " ".join(m.get("mongo / secrets", []))
            assert (not m) if attendu is None else attendu in texte, (change, m)
            assert "u:p@" not in texte, f"l'URI est affichée : {texte}"
    finally:
        for k, v in sauve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_compte_au_hash_malforme_refuse():
    """Un mdp_hash qui n'a pas la forme werkzeug : personne ne pourrait se
    connecter avec ce compte, et login() ne dirait rien."""
    import config
    import store
    for h in ("pas-un-hash", "x$y$z", "scrypt:1$a$b"):   # forme, puis méthode inconnue
        store.db().comptes.insert_one({"_id": "casse", "mdp_hash": h})
        try:
            assert "casse" in " ".join(config._verifier_comptes().get("comptes", [])), \
                (h, config._verifier_comptes())
        finally:
            store.db().comptes.delete_one({"_id": "casse"})
    assert not config._verifier_comptes(), "contrôle : sans lui, plus de manque"


def test_bilan_uri_refusee_par_pymongo_est_un_manque():
    """Port non numérique, options sans « / », srv multi-hôtes : pymongo lève
    (ValueError, ConfigurationError, InvalidURI) en pleine vérification. Le
    bilan doit le dire, sans trace et sans le mot de passe de l'URI."""
    ecrire()
    vraie = os.environ["MONGO_URI"]
    try:
        for uri in ("mongodb://u:mdp-uri@h:abc/", "mongodb://u:mdp-uri@h?x=1",
                    "mongodb+srv://u:mdp-uri@h1,h2/"):
            os.environ["MONGO_URI"] = uri
            code, sortie = lancer()
            assert code == 1 and "base mongo illisible" in sortie, (uri, sortie)
            assert "mdp-uri" not in sortie, sortie
            assert "comptes" not in sortie, "le même manque redit par les comptes"
    finally:
        os.environ["MONGO_URI"] = vraie


def test_bilan_refuse_la_base_en_memoire_hors_demo():
    """mongomock:// recopié dans le .env d'un client : base vidée à chaque
    redémarrage. Refusé tant que BASE_EN_MEMOIRE n'est pas posé."""
    ecrire()
    code, sortie = lancer(sans=("BASE_EN_MEMOIRE",))
    assert code == 1 and "base en mémoire" in sortie, sortie


def test_bilan_refuse_un_modele_de_mail_absent():
    """Le mail « demande validée » manquait à une config réelle : l'envoi
    échouait à chaque validation, noté au journal, sans que le bilan le dise."""
    ecrire()
    (CONF / "mails" / "demande_validee.txt").unlink()
    code, sortie = lancer()
    assert code == 1 and "mails/demande_validee.txt absent" in sortie, sortie
    assert "[config/mails/]" in sortie, sortie
    ecrire()
    (CONF / "mails" / "rejet.txt").write_text("  \n", encoding="utf-8")
    code, sortie = lancer()
    assert code == 1 and "mails/rejet.txt vide" in sortie, sortie


def test_recap_hebdo_absent_avertit_sans_bloquer():
    """Mail du cron (recap.py), pas d'un dossier : son echec s'affiche deja en
    bandeau sur /salaries. Une instance en place ne doit pas refuser de repartir."""
    ecrire(PROD, SOCIETES_PROD)
    (CONF / "mails" / "recap_hebdo.txt").unlink()
    code, sortie = lancer()
    avert = [l for l in sortie.splitlines() if "⚠" in l]
    assert code == 0 and len(avert) == 1 and "recap_hebdo.txt absent" in avert[0], sortie


def test_bilan_refuse_un_id_de_champ_avec_point():
    """store ecrit `champs.<id>` : « a.b » y creerait un sous-document."""
    ecrire()
    f = json.loads(json.dumps(FORMULAIRE))
    f["champs"].append({"id": "a.b", "libelle": "X", "type": "texte"})
    (CONF / "formulaire.json").write_text(json.dumps(f), encoding="utf-8")
    code, sortie = lancer()
    assert code == 1 and "« a.b »" in sortie and "[config/formulaire.json]" in sortie, sortie


def test_bilan_refuse_les_secrets_absents_de_l_environnement():
    """Les secrets vivent dans l'environnement (.env écrit par installer.py),
    jamais dans config/ : chacun manquant sort un KO qui nomme la variable et
    renvoie au .env, pas à instance.json."""
    ecrire()
    code, sortie = lancer(sans=("SECRET_KEY",))
    assert code == 1 and "SECRET_KEY absent" in sortie and "[fichier d'environnement .env" in sortie, sortie

    ecrire({**INSTANCE, "signature": {"mode": "yousign"}})
    code, sortie = lancer(sans=("YOUSIGN_CLE_API",))
    assert code == 1 and "! signature  [fichier d'environnement .env]" in sortie, sortie
    assert "YOUSIGN_CLE_API" in sortie, sortie

    ecrire({**INSTANCE, "mails": {**INSTANCE["mails"], "mode": "smtp", "utilisateur": "u@relais"}})
    code, sortie = lancer(sans=("SMTP_MOT_DE_PASSE",))
    assert code == 1 and "! SMTP_MOT_DE_PASSE  [fichier d'environnement .env]" in sortie, sortie

    # contrôle : la même config passe quand la variable est là
    os.environ["SMTP_MOT_DE_PASSE"] = "x"
    try:
        code, sortie = lancer()
    finally:
        del os.environ["SMTP_MOT_DE_PASSE"]
    assert "SMTP_MOT_DE_PASSE" not in sortie, sortie
    ecrire()


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
