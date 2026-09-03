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
FORMULAIRE = {
    # Le moteur ne lit que des roles : ce formulaire nomme son champ « nom »,
    # la table `roles` fait le pont. Sans elle, verifier() refuse de servir.
    "roles": {"nom": "nom"},
    "champs": [
    {"id": "etablissement", "libelle": "Établissement", "type": "etablissement",
     "requis": True},
    {"id": "email_demandeur", "libelle": "Votre email", "type": "email",
     "requis": True},
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


def ecrire(instance=None, contrat="Bonjour {{Nom}}, poste {{Poste}}.",
           formulaire=None):
    (CONF / "instance.json").write_text(
        json.dumps(instance or INSTANCE, ensure_ascii=False), encoding="utf-8")
    (CONF / "formulaire.json").write_text(
        json.dumps(formulaire or FORMULAIRE), encoding="utf-8")
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


def test_role_mal_declare_refuse_au_demarrage():
    """Les roles sont la promesse « n'importe quelle PME » : le moteur ne lit
    plus aucun id de champ en dur. Un role mal declare doit donc se payer au
    DEMARRAGE -- sinon c'est un dossier vide ou un mail sans destinataire des
    mois plus tard. Comme pour `derives`, on verifie la FORME avant les valeurs :
    un `roles` qui n'est pas un dict ferait exploser la boucle qui le lit, et le
    garde-fou tomberait au lieu de refuser proprement."""
    def refus(roles, attendu):
        ecrire(formulaire={**FORMULAIRE, "roles": roles})
        manques = config.verifier()
        assert "roles" in manques, (roles, manques)
        assert attendu in " ".join(manques["roles"]), (attendu, manques)

    refus(["nom", "nom"], "attend un objet")          # forme : liste, pas dict
    refus("nom=nom", "attend un objet")               # forme : chaine
    refus({"nom": "nom", "surnom": "nom"}, "« surnom » inconnu")
    refus({"nom": "champ_absent"}, "n'est pas un champ du formulaire")
    # Forme de la VALEUR, pas seulement de la table : « cid not in ids » leve un
    # TypeError sur une liste ou un dict. Le garde-fou tombait au lieu de
    # refuser -- meme faute que les regles `derives` avant dfde911.
    refus({"nom": ["nom"]}, "n'est pas un id de champ")
    refus({"nom": {"id": "nom"}}, "n'est pas un id de champ")
    refus({"nom": 42}, "n'est pas un id de champ")
    refus({"nom": ""}, "n'est pas un id de champ")
    refus({}, "aucun champ « nom_naissance »")        # requis, non declare

    # Un client qui renomme TOUT : seuls les roles relient son formulaire.
    ecrire(formulaire={
        "roles": {"etablissement": "chantier", "poste": "fonction",
                  "nom": "identite", "email": "courriel"},
        "champs": [
            {"id": "chantier", "libelle": "Chantier", "type": "etablissement",
             "requis": True},
            {"id": "courriel", "libelle": "Email", "type": "email", "requis": True},
            {"id": "identite", "libelle": "Nom", "type": "texte", "requis": True,
             "placeholder": "Nom"},
            {"id": "fonction", "libelle": "Poste", "type": "choix", "requis": True,
             "options": ["Manager"], "placeholder": "Poste"}]})
    assert config.verifier() == {}, config.verifier()


def test_moteur_ne_lit_aucun_id_de_champ_en_dur():
    """Controle au niveau des APPELANTS, pas de config.role() seul : le pont ne
    sert a rien si un module lit encore « etablissement » ou « poste » en dur.
    Chaque appel ci-dessous cassait avant les roles."""
    import contrat
    import doctor
    import recap
    ecrire(formulaire={
        "roles": {"etablissement": "chantier", "poste": "fonction",
                  "nom": "identite_nom", "prenom": "identite_prenom",
                  "email": "courriel", "date_debut": "demarrage"},
        "champs": [
            {"id": "chantier", "libelle": "Chantier", "type": "etablissement",
             "requis": True},
            {"id": "courriel", "libelle": "Email", "type": "email", "requis": True},
            {"id": "identite_nom", "libelle": "Nom", "type": "texte", "requis": True,
             "placeholder": "Nom"},
            {"id": "identite_prenom", "libelle": "Prénom", "type": "texte"},
            {"id": "fonction", "libelle": "Poste", "type": "choix", "requis": True,
             "options": ["Manager"], "placeholder": "Poste"},
            {"id": "demarrage", "libelle": "Début", "type": "date"}]})
    assert config.verifier() == {}, config.verifier()

    dossier = {"chantier": "ACME / Siège", "fonction": "Manager",
               "identite_nom": "Nkemba", "identite_prenom": "Awa",
               "courriel": "manager@example.test", "demarrage": "2026-12-01"}

    assert config.modele_pour(dossier) == "contrat.txt", "poste lu en dur"
    assert config.valeur(dossier, "etablissement") == "ACME / Siège"
    vals = contrat.valeurs(dossier, config.mentions(dossier["chantier"]))
    assert vals["NomPrenom"] == "Awa NKEMBA", vals["NomPrenom"]
    assert vals["Poste"] == "Manager", vals
    # Ce client n'a AUCUN champ « nom de naissance » : le placeholder doit
    # retomber sur le role nom, pas sortir vide dans un contrat signe.
    assert vals["NomNaissanceUsage"] == "NKEMBA", vals["NomNaissanceUsage"]

    ligne = recap.ligne({"champs": dossier, "journal": []})
    assert "NKEMBA" in ligne and "Manager" in ligne and "Siège" in ligne, ligne

    lignes = doctor.examiner()
    assert [l["statut"] for l in lignes] == ["ok"], lignes


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
