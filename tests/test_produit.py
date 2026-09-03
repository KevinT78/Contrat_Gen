"""Ce qui fait de ce dépôt un produit installable chez un autre client.

    python tests/test_produit.py

Trois décisions, trois vérifications :

  - le CLIENT balise lui-même ses modèles -> une faute de frappe sur un jeton
    (« {{ Nom }} », « {{nom}} ») doit être refusée AU DÉMARRAGE, pas exploser à
    la génération du contrat d'un vrai salarié ;
  - la fiche des jetons remise au client est DÉRIVÉE de sa config, jamais
    rédigée à la main ;
  - le code est un paquet versionné, config/ vit chez le client -> un format de
    config inconnu refuse de servir, et un rechargement à chaud raté laisse
    l'ancienne config en place plutôt que de casser une instance qui servait.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(tempfile.mkdtemp(prefix="contratgen-produit-"))
os.environ["CONFIG_DIR"] = str(BASE / "config")
os.environ["DONNEES"] = str(BASE / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash          # noqa: E402
import config                                                 # noqa: E402
import placeholders                                           # noqa: E402

CONF = BASE / "config"
(CONF / "contrats").mkdir(parents=True)

INSTANCE = {
    "config_version": 1,
    "client": "ACME v1",
    "secret": "a" * 64,
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "", "rh": [], "superviseur": "",
              "dpae": None, "recap": None, "comptable_defaut": "c@acme.example"},
    "utilisateurs": {"rh": {"mdp_hash": generate_password_hash("pas-demo"),
                            "admin": True}},
    "motifs_ko": ["Autre"],
    "templates": {"Manager": "contrat.txt"},
}
FORMULAIRE = {"champs": [
    {"id": "nom", "libelle": "Nom", "type": "texte", "requis": True,
     "placeholder": "Nom"},
    {"id": "poste", "libelle": "Poste", "type": "choix", "requis": True,
     "options": ["Manager"], "placeholder": "Poste"},
]}
SOCIETES = [{"nom": "ACME", "siren": "111 111 111",
             "comptable_email": "c@acme.example",
             "mentions": {"RaisonSociale": "ACME SAS"},
             "etablissements": [{"nom": "Siège", "siret": "111 111 111 00011",
                                 "mentions": {"AdresseEtablissement": "1 rue X"}}]}]


def ecrire(instance=None, contrat="Bonjour {{Nom}}, poste {{Poste}}."):
    (CONF / "instance.json").write_text(
        json.dumps(instance or INSTANCE, ensure_ascii=False), encoding="utf-8")
    (CONF / "formulaire.json").write_text(json.dumps(FORMULAIRE), encoding="utf-8")
    (CONF / "societes.json").write_text(json.dumps(SOCIETES), encoding="utf-8")
    (CONF / "contrats" / "contrat.txt").write_text(contrat, encoding="utf-8")
    config._CACHE.clear()


def test_config_saine_sert():
    ecrire()
    assert config.verifier() == {}, config.verifier()


def test_jeton_mal_ecrit_refuse_au_demarrage():
    """Le mode d'échec de « c'est le client qui balise » : l'espace et la casse."""
    for mauvais in ("{{ Nom }}", "{{nom}}", "{{Nom }}"):
        ecrire(contrat=f"Bonjour {mauvais}, poste {{{{Poste}}}}.")
        manques = config.verifier()
        assert "contrat.txt" in manques, f"{mauvais} passe le garde-fou"
        raison = " ".join(manques["contrat.txt"])
        assert "écrire exactement {{Nom}}" in raison, raison

    ecrire(contrat="Bonjour {{Inconnu}}.")
    assert "aucune source" in " ".join(config.verifier()["contrat.txt"])


def test_fiche_des_jetons_derivee_de_la_config():
    ecrire()
    texte = placeholders.fiche()
    assert "`{{Nom}}` | Question « Nom »" in texte, texte
    assert "`{{Siret}}`" in texte and "`{{RaisonSociale}}`" in texte
    assert "config/contrats/contrat.txt" in texte
    # un jeton que rien n'alimente n'a rien à faire dans la fiche du client
    assert "{{Inconnu}}" not in texte


def test_derive_mal_ecrite_refusee_au_demarrage():
    """Meme argument que pour les jetons : une regle malformee levait un
    KeyError a la generation d'un vrai contrat, devant la RH.

    Chaque cas est une forme de regle qui CASSAIT ailleurs :
    _appliquer_derives lit d["placeholder"], d["de"], d["alors"] sans garde, et
    _teste depaquete `si` en trois. Le dernier cas est le garde-fou teste contre
    lui-meme : `si: ["nom"]` faisait lever un IndexError DANS verifier()."""
    def refus(derive, attendu):
        ecrire({**INSTANCE, "derives": [derive]})
        manques = config.verifier()
        sujet = next(s for s in manques if s.startswith("derives"))
        assert attendu in " ".join(manques[sujet]), (attendu, manques)

    refus({"placeholder": "Nom", "format": "mensualisé", "de": "nom"}, "formateur")
    refus({"placeholder": "Nom", "format": "lettres"}, "« de »")
    refus({"format": "lettres", "de": "nom"}, "placeholder")
    refus({"placeholder": "Nom", "si": ["nom", "égal", "x"], "alors": "a"}, "opérateur")
    refus({"placeholder": "Nom", "si": ["nom"], "alors": "a"}, "[champ, opérateur")
    refus({"placeholder": "Nom", "si": "nom == x", "alors": "a"}, "[champ, opérateur")
    refus({"placeholder": "Nom", "si": ["nom", "==", "x"]}, "ne produit rien")

    ecrire({**INSTANCE, "derives": [
        {"placeholder": "Nom", "si": ["nom", "==", "x"], "alors": "a"}]})
    assert config.verifier() == {}, config.verifier()
    ecrire({**INSTANCE, "derives": [{"placeholder": "Nom", "alors": "toujours"}]})
    assert config.verifier() == {}, config.verifier()


def test_format_de_config_inconnu_refuse():
    ecrire({**INSTANCE, "config_version": 99})
    assert "config_version" in config.verifier()


def test_rechargement_rate_garde_lancienne_config():
    ecrire()
    assert config.verifier() == {}
    assert config.instance()["client"] == "ACME v1"

    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "client": "ACME v2", "config_version": 99}),
        encoding="utf-8")
    assert "config_version" in config.recharger()
    assert config.instance()["client"] == "ACME v1", \
        "une config invalide a pris la place de celle qui servait"

    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "client": "ACME v2"}), encoding="utf-8")
    assert config.recharger() == {}
    assert config.instance()["client"] == "ACME v2", "le rechargement n'a rien changé"


def test_rechargement_met_a_jour_ce_qui_est_fige_a_limport():
    """Contrôle au niveau de l'APPELANT, pas de config.recharger() seul :
    recharger ne sert à rien si les valeurs lues une fois à l'import restent
    celles d'avant. Deux l'étaient — la clé de signature des cookies (qui doit
    rester alignée sur celle de store.signer, sinon les liens du lot cassent en
    silence) et les motifs de KO."""
    ecrire()
    (BASE / "data" / "documents").mkdir(parents=True, exist_ok=True)
    (BASE / "data" / "soumissions").mkdir(parents=True, exist_ok=True)
    import app as application

    application.app.secret_key = config.instance()["secret"]
    client = application.app.test_client()
    with client.session_transaction() as s:
        s["utilisateur"] = "rh"

    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "secret": "b" * 64,
                    "motifs_ko": ["Motif tout neuf"]}), encoding="utf-8")
    assert client.post("/recharger", follow_redirects=True).status_code == 200
    assert application.app.secret_key == "b" * 64, \
        "clé de session restée sur l'ancien secret : les liens signés divergent"
    assert config.instance()["motifs_ko"] == ["Motif tout neuf"], \
        "motifs de KO figés à l'import"


if __name__ == "__main__":
    import shutil
    try:
        for nom, fn in sorted(globals().items()):
            if nom.startswith("test_"):
                fn()
                print(f"  ✓ {nom}")
        print("\nProduit installable : garde-fou de balisage, fiche dérivée, "
              "format de config versionné, rechargement à chaud sûr.")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
