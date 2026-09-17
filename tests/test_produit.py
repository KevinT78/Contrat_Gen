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
import subprocess
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
    "secret": "a" * 64, "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "rh@acme.example", "rh": ["rh@acme.example"],
              "dpae": None, "recap": None, "comptable_defaut": "c@acme.example"},
    "utilisateurs": {"rh": {"mdp_hash": generate_password_hash("pas-demo")}},
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
           formulaire=None, grille=None):
    (CONF / "instance.json").write_text(
        json.dumps(instance or INSTANCE, ensure_ascii=False), encoding="utf-8")
    fg = CONF / "grille.json"
    fg.write_text(json.dumps(grille, ensure_ascii=False), encoding="utf-8") \
        if grille else fg.unlink(missing_ok=True)
    (CONF / "formulaire.json").write_text(
        json.dumps(formulaire or FORMULAIRE), encoding="utf-8")
    (CONF / "societes.json").write_text(json.dumps(SOCIETES), encoding="utf-8")
    (CONF / "contrats" / "contrat.txt").write_text(contrat, encoding="utf-8")
    config._CACHE.clear()


def test_config_saine_sert():
    ecrire()
    assert config.verifier() == {}, config.verifier()


def test_mails_rh_vide_refuse_au_demarrage():
    """Deux instances de verification ont servi avec mails.rh vide : chaque
    soumission finissait en mail_echoue, verifier() disant OK."""
    for mails in ({**INSTANCE["mails"], "rh": []}, {**INSTANCE["mails"], "rh": [""]},
                  {**INSTANCE["mails"], "expediteur": ""}):
        ecrire(instance={**INSTANCE, "mails": mails})
        assert "mails" in config.verifier(), f"{mails!r} passe le garde-fou"


def test_utilisateurs_malforme_refuse_au_demarrage():
    """Le bloc `utilisateurs` de instance.json est la seule source des comptes :
    une forme cassée doit refuser le démarrage, pas planter login()."""
    for mauvais in ({}, "pas-un-dict", {"rh": "pas-un-dict"},
                    {"rh": {"role": "rh"}}, {"rh": {"mdp_hash": ""}}):
        ecrire(instance={**INSTANCE, "utilisateurs": mauvais})
        # verifier() ne doit pas planter et doit signaler « comptes »
        assert "comptes" in config.verifier(), f"{mauvais!r} passe le garde-fou"


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


def test_exclusion_volontaire_en_forme_dict():
    """`{"Manager": null}` dit la même chose que la règle liste `modele: null` :
    un cas identifié, sans contrat à produire chez ce client.

    Le null était pris pour un nom de fichier : `config/contrats/None` levait
    un TypeError AU DÉMARRAGE (_verifier_placeholders), et la fiche des jetons
    mourait dans sorted() -- un client qui exclut un poste en forme dict ne
    pouvait plus lancer l'application du tout."""
    ecrire(instance={**INSTANCE, "templates": {"Manager": None}})

    assert config.verifier() == {}, config.verifier()
    assert None not in config._templates_actifs()

    # Les deux None de modele_pour restent distinguables (ce que lit doctor) :
    # règle trouvée mais sans modèle  vs  aucune règle ne vise ce cas.
    regle = config.regle_pour({"poste": "Manager"})
    assert regle is not None and regle["modele"] is None, regle
    assert config.regle_pour({"poste": "Stagiaire"}) is None

    assert "None" not in placeholders.fiche(), placeholders.fiche()


def test_modele_generique_de_fiche_absent_refuse_au_demarrage():
    """La fiche salarié est TOUJOURS produite : sans clé `fiche_salarie`, au
    modèle générique versé avec le code (`modeles/fiche_salarie.docx`).

    Un déploiement où `modeles/` n'a pas été copié démarrait donc VERT, puis
    échouait à chaque validation (« Fiche salarié non générée : modèle
    absent ») -- devant la RH, dossier par dossier. Le garde-fou de démarrage
    existe pour dire ça à l'installation."""
    ecrire()                                   # aucune clé `fiche_salarie`
    assert config.verifier() == {}, config.verifier()      # modeles/ est dans le dépôt

    vrai = config.MODELE_FICHE_GENERIQUE
    config.MODELE_FICHE_GENERIQUE = BASE / "modeles" / "jamais-copie.docx"
    try:
        manques = config.verifier()
    finally:
        config.MODELE_FICHE_GENERIQUE = vrai
    sujet = next((s for s in manques if "fiche" in s.casefold()), None)
    assert sujet, manques
    assert "absent" in " ".join(manques[sujet]).casefold(), manques


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


def test_regle_template_incoherente_refusee_au_demarrage():
    """Une regle `templates` (forme liste) dont une valeur de « quand » ne peut
    JAMAIS correspondre a un dossier reel passait sans message : le mauvais
    modele partait en silence sur la regle fourre-tout suivante. Cas reel : la
    cle d'un etablissement est « Societe / Etablissement » (voir
    config.etablissements()), pas le nom du site seul."""
    # -- valeurs : a travers le bilan complet (les regles sont bien formees). --
    def refus(quand, attendu):
        ecrire(instance={**INSTANCE, "templates": [
            {"quand": quand, "modele": None}, {"modele": "contrat.txt"}]})
        manques = config.verifier()
        sujet = next((s for s in manques if s.startswith("templates")), None)
        assert sujet, (quand, manques)
        assert attendu in " ".join(manques[sujet]), (attendu, manques)

    # nom du site seul au lieu de la cle complete "Societe / Etablissement"
    refus({"etablissement": "Siège"}, "écrire exactement « ACME / Siège »")
    # etablissement qui n'existe nulle part
    refus({"etablissement": "Roissy"}, "n'est pas une clé d'établissement")
    # option mal orthographiee sur un champ a choix
    refus({"poste": "Manageur"}, "Manager")
    # champ inconnu
    refus({"champ_absurde": "x"}, "n'est pas un champ du formulaire")

    # cas valide : la cle complete est acceptee (poste inclus pour ne pas
    # declencher, en plus, le garde-fou separe « aucun poste ne vise Manager »)
    ecrire(instance={**INSTANCE, "templates": [
        {"quand": {"etablissement": "ACME / Siège", "poste": "Manager"},
         "modele": "contrat.txt"}]})
    assert config.verifier() == {}, config.verifier()

    # modele null (exclusion volontaire) : la regle reste verifiee, pas refusee
    ecrire(instance={**INSTANCE, "templates": [
        {"quand": {"etablissement": "ACME / Siège", "poste": "Manager"},
         "modele": None},
        {"modele": "contrat.txt"}]})
    assert config.verifier() == {}, config.verifier()

    # forme dict (client simple) : pas de « quand », rien a verifier ici
    ecrire()
    assert config.verifier() == {}, config.verifier()

    # -- forme : a travers le bilan complet aussi. Les verificateurs voisins
    # (_templates_actifs, _verifier_postes) plantaient sur ces formes au lieu de
    # laisser _verifier_regles les refuser. --
    for templates, attendu in (
            ([{"quand": "poste=Manager", "modele": None}], "« quand » attend un objet"),
            (["pas-un-objet"], "attend un objet")):
        ecrire(instance={**INSTANCE, "templates": templates})
        manques = config.verifier()
        sujet = next((s for s in manques if s.startswith("templates")), None)
        assert sujet and attendu in " ".join(manques[sujet]), (templates, manques)


def test_poste_sans_sa_ligne_de_grille_refuse_au_demarrage():
    """Le salaire est le champ dont personne ne doute en relisant un contrat.

    La grille reconnait un poste par mots normalises, la ligne la plus
    specifique gagne. Un poste prive de SA ligne ne sort donc pas un contrat
    vide -- il sort au tarif d'un poste dont l'intitule est contenu dans le
    sien, et ni doctor ni la RH ne le voient. Mesure faite en montant
    « Toitures du Nord » (SCENARIOS.md) : « Apprenti couvreur » retire de la
    grille etait paye au tarif « Couvreur », doctor tout vert."""
    import contrat as moteur

    def form(*options):
        return {**FORMULAIRE,
                "champs": [{**c, "options": list(options)} if c["id"] == "poste" else c
                           for c in FORMULAIRE["champs"]]}

    TROIS = form("Couvreur", "Apprenti couvreur", "Chef d'équipe")
    GRILLE = {"postes": [{"poste": "Couvreur", "mensuel": 1900},
                         {"poste": "Apprenti couvreur", "mensuel": 950},
                         {"poste": "Chef d'équipe", "mensuel": 2650}]}
    ecrire(formulaire=TROIS, grille=GRILLE)
    assert config.verifier() == {}, config.verifier()

    # 1. La ligne de l'apprenti disparait -> il herite de celle du couvreur.
    ampute = {"postes": [e for e in GRILLE["postes"] if e["poste"] != "Apprenti couvreur"]}
    assert moteur.salaire({"poste": "Apprenti couvreur"}, ampute) \
        == moteur.salaire({"poste": "Couvreur"}, ampute), \
        "le tarif herite ne se produit plus : ce test ne prouve plus rien"
    ecrire(formulaire=TROIS, grille=ampute)
    manques = config.verifier()
    sujet = "grille — postes « Couvreur » et « Apprenti couvreur »"
    assert sujet in manques, manques
    assert "sa propre ligne" in " ".join(manques[sujet]), manques

    # 2. Un poste qu'aucune ligne ne vise (aucun mot en commun) : vide, pas faux.
    sans_chef = {"postes": [e for e in GRILLE["postes"] if e["poste"] != "Chef d'équipe"]}
    ecrire(formulaire=TROIS, grille=sans_chef)
    assert "ne le rémunère" in " ".join(
        config.verifier()["grille — poste « Chef d'équipe »"]), config.verifier()

    # 3. La FORME avant les valeurs : sans ce garde, une ligne sans « poste »
    #    faisait planter le garde-fou (KeyError) au lieu de refuser.
    for mauvaise in ({"postes": [{"mensuel": 900}]}, {"postes": {"Couvreur": 1900}},
                     {"postes": ["Couvreur"]}, {"postes": [{"poste": 42}]}):
        ecrire(formulaire=TROIS, grille=mauvaise)
        assert "grille.json" in config.verifier(), mauvaise

    # 4. Le match partiel VOULU reste legal : le client n'a pas fait plus fin
    #    qu'« Equipier » et ses equipiers polyvalents prennent cette ligne.
    ecrire(formulaire=form("Equipier Polyvalent"),
           grille={"postes": [{"poste": "Equipier", "mensuel": 1867}]})
    assert config.verifier() == {}, config.verifier()


def test_duree_hors_bareme_refusee_au_demarrage():
    """Chaque duree hebdo proposee par le formulaire doit avoir sa ligne de
    bareme. Mesure du 2026-09-14 sur config.demo : 9 durees proposees, 2 au
    bareme, bilan OK -- doctor n'essaie que la PREMIERE option, et elle etait
    couverte. Le premier vrai salarie a 20H voyait son contrat refuse devant la
    RH. Le piege est rejoue tel quel : la duree couverte en premier."""
    ligne = {"chiffres": "1 280,24", "lettres": "mille deux cent quatre-vingts euros"}

    def form(type_heures="choix", options=("24H", "20H")):
        champ = {"id": "temps_travail", "libelle": "Temps de travail",
                 "type": type_heures, "requis": True}
        if type_heures == "choix":
            champ["options"] = list(options)
        return {**FORMULAIRE, "champs": [
            {**c, "options": ["Equipier"]} if c["id"] == "poste" else c
            for c in FORMULAIRE["champs"]] + [champ]}

    def grille(**over):
        return {"champ_heures": "temps_travail",
                "postes": [{"poste": "Equipier", "bareme": {"24": ligne}}], **over}

    ecrire(formulaire=form(), grille=grille())
    manques = config.verifier()
    sujet = next((s for s in manques if "Equipier" in s and "grille" in s), None)
    assert sujet and "20H" in " ".join(manques[sujet]), manques
    assert "24H" not in " ".join(manques[sujet]), manques

    ecrire(formulaire=form(), grille=grille(postes=[
        {"poste": "Equipier", "bareme": {"24": ligne, "20": ligne}}]))
    assert config.verifier() == {}, config.verifier()

    # Un montant que contrat._nombre ne sait pas lire tombait a 0 EN SILENCE :
    # le contrat sortait signe avec « (zéro) » en toutes lettres. La FORME
    # (« chiffres » non vide) ne suffit pas, il faut que ca fasse un nombre.
    for illisible in ("", "  ", "a partir de 1500", "-", "1.2.3"):
        mauvais = {**ligne, "chiffres": illisible}
        ecrire(formulaire=form(), grille=grille(postes=[
            {"poste": "Equipier", "bareme": {"24": mauvais, "20": ligne}}]))
        sujet = next((s for s in config.verifier() if "grille" in s), None)
        assert sujet, f"« {illisible} » passe le garde-fou du barème"

    # Les formes a REFUSER, sans planter le garde-fou.
    for mauvaise in (grille(champ_heures=None), grille(champ_heures="inexistant"),
                     {"postes": [{"poste": "Equipier", "bareme": {"24": ligne}}]},
                     grille(postes=[{"poste": "Equipier", "bareme": ["24"]}]),
                     grille(postes=[{"poste": "Equipier", "bareme": {"24": ligne, "20": "x"}}]),
                     # "chiffres" reste la seule cle exigee ("lettres" est
                     # desormais derivee, pas saisie) -- une ligne qui n'en a
                     # pas doit toujours etre refusee.
                     grille(postes=[{"poste": "Equipier",
                                     "bareme": {"24": ligne, "20": {"lettres": "dix"}}}])):
        ecrire(formulaire=form(), grille=mauvaise)
        assert any("grille" in s for s in config.verifier()), mauvaise

    # « mensuel » l'emporte sur « bareme » a la generation : pas de refus.
    ecrire(formulaire=form(), grille=grille(postes=[
        {"poste": "Equipier", "mensuel": 1900, "bareme": {"24": ligne}}]))
    assert config.verifier() == {}, config.verifier()

    # Duree en saisie libre : rien a enumerer, pas de refus.
    ecrire(formulaire=form("nombre"), grille=grille())
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

    ligne = recap.ligne({"id": "X", "champs": dossier, "journal": []})
    assert "NKEMBA" in ligne and "Manager" in ligne and "Siège" in ligne, ligne
    assert "http://localhost/lot/lot_comptable.X." in ligne, ligne

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


def test_demarrer_ne_cree_pas_le_dossier_de_stockage_quil_verifie():
    """Le garde-fou constate, il ne répare pas.

    `demarrer()` crée `soumissions/` et `DOSSIERS/` sous config.DONNEES. Tant
    que ce mkdir(parents=True) précédait `verifier()`, il créait LUI-MÊME le
    chemin du drive annoncé dans instance.json : _verifier_stockage trouvait
    alors un dossier bien présent et accessible, l'app démarrait, et les pièces
    d'identité partaient dans un dossier local qui ressemble à un dossier
    synchronisé. Le contrôle porte sur ce qui est observable : refus de servir
    ET dossier toujours absent."""
    ecrire()
    absent = BASE / "drive-jamais-replique"
    assert not absent.exists()
    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "stockage": {"mode": "dossier", "chemin": str(absent)}}),
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "DONNEES"}
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        # timeout obligatoire : quand la régression est là, demarrer() ne sort
        # pas — il SERT. Sans lui, ce test pendrait au lieu de virer au rouge.
        r = subprocess.run(
            [sys.executable, "-c", "import app; app.demarrer()"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    except subprocess.TimeoutExpired:
        raise AssertionError("l'app s'est mise à servir alors que le dossier de "
                             "stockage n'existe pas")
    assert r.returncode != 0, f"l'app a démarré sur un drive absent :\n{r.stdout}"
    assert "stockage" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not absent.exists(), "demarrer() a créé le dossier qu'il devait refuser"
    config._CACHE.clear()


def test_rechargement_dit_que_le_stockage_ne_bouge_pas_a_chaud():
    """config.DONNEES est figé à l'import : changer `stockage` puis recharger
    ne déplace ni le chemin ni les fichiers. Faire semblant serait pire que ne
    rien faire — le handler doit le dire."""
    ecrire()
    import app as application
    application.app.secret_key = config.instance()["secret"]
    client = application.app.test_client()
    with client.session_transaction() as s:
        s["utilisateur"] = "rh"

    ailleurs = BASE / "drive-bis"
    ailleurs.mkdir(exist_ok=True)
    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "stockage": {"mode": "dossier", "chemin": str(ailleurs)}}),
        encoding="utf-8")
    donnees = os.environ.pop("DONNEES")        # sinon l'env l'emporte, rien ne bouge
    try:
        page = client.post("/recharger", follow_redirects=True)
    finally:
        os.environ["DONNEES"] = donnees
    assert page.status_code == 200
    assert "redémarrez" in page.get_data(as_text=True), \
        "stockage changé à chaud sans un mot : les données vont ailleurs que promis"
    ecrire()
    config.recharger()


def test_verifier_conservation_refuse_les_formes_invalides():
    """La FORME avant les valeurs : une config `conservation` malformée doit
    sortir un KO propre qui nomme le sujet, jamais faire planter le garde-fou.
    Bloc absent = pas de purge (rétro-compatible)."""
    ecrire()
    assert "conservation" not in config.verifier(), "bloc absent doit passer"

    for bloc in ({"jours": True, "jours_candidature": 730, "apres": ["Rejetee"]},
                 {"jours": 0, "jours_candidature": 730, "apres": ["Rejetee"]},
                 {"jours": "1095", "jours_candidature": 730, "apres": ["Rejetee"]},
                 {"jours": 1095, "jours_candidature": 730, "apres": []},
                 {"jours": 1095, "jours_candidature": 730, "apres": ["Inexistant"]},
                 {"jours": 1095, "apres": ["Rejetee"]},          # jours_candidature manquant
                 "pas-un-objet"):
        ecrire(instance={**INSTANCE, "conservation": bloc})
        manques = config.verifier()               # ne doit pas lever
        assert "conservation" in manques, f"{bloc!r} passe le garde-fou"

    # le cas valide passe
    ecrire(instance={**INSTANCE, "conservation": {
        "jours": 1095, "jours_candidature": 730,
        "apres": ["RemisComptable", "Rejetee", "Abandonnee"]}})
    assert "conservation" not in config.verifier(), config.verifier()


def test_second_serveur_sur_le_meme_stockage_refuse():
    """store._verrou est intra-process : deux serveurs sur le même stockage
    perdent des entrées de journal en silence. Le fichier .serveur-actif.json
    refuse le second démarrage tant qu'il est frais."""
    ecrire()
    config.DONNEES.mkdir(parents=True, exist_ok=True)
    import app as application
    import json as _j
    import time as _t
    f = application._fichier_verrou()
    f.unlink(missing_ok=True)

    application._verrou_serveur(("machine-A", 111))
    assert f.exists()
    try:
        application._verrou_serveur(("machine-B", 222))
        raise AssertionError("un second serveur a démarré sur le même stockage")
    except SystemExit as e:
        assert "machine-A" in str(e) and "111" in str(e), str(e)
    application._verrou_serveur(("machine-A", 111))          # même couple : refresh, OK

    # contrôle négatif 1 : verrou d'une AUTRE machine, périmé au-delà du TTL
    f.write_text(_j.dumps({"hote": "machine-A", "pid": 111,
                           "le": _t.time() - application._VERROU_TTL - 10}), encoding="utf-8")
    application._verrou_serveur(("machine-B", 222))          # périmé -> pas de refus
    assert _j.loads(f.read_text())["hote"] == "machine-B", "le verrou périmé n'a pas été repris"

    # contrôle négatif 2 : verrou de CETTE machine dont le pid est mort -> repris
    # aussitôt (pas d'attente du TTL). 2**31-1 : pid qui n'existe pas.
    import socket as _s
    f.write_text(_j.dumps({"hote": _s.gethostname(), "pid": 2**31 - 1,
                           "le": _t.time()}), encoding="utf-8")
    application._verrou_serveur()                            # pid mort -> pas de refus
    assert _j.loads(f.read_text())["pid"] == os.getpid(), "verrou d'un pid mort non repris"

    # et le contrôle positif : pid VIVANT (le nôtre) sur cette machine -> refus
    f.write_text(_j.dumps({"hote": _s.gethostname(), "pid": os.getpid() ,
                           "le": _t.time()}), encoding="utf-8")
    try:
        application._verrou_serveur((_s.gethostname(), os.getpid() + 1))
        raise AssertionError("verrou tenu par un pid vivant : le refus doit tomber")
    except SystemExit:
        pass
    f.unlink(missing_ok=True)


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
