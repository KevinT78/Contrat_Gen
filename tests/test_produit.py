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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(tempfile.mkdtemp(prefix="contratgen-produit-"))
os.environ["CONFIG_DIR"] = str(BASE / "config")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _outils import base_mongo, societes as poser_societes  # noqa: E402
base_mongo("produit")

from werkzeug.security import generate_password_hash          # noqa: E402
import apparence                                              # noqa: E402
import config                                                 # noqa: E402
import placeholders                                           # noqa: E402
import store                                                  # noqa: E402

COMPTES = {"rh": {"mdp_hash": generate_password_hash("pas-demo")}}

CONF = BASE / "config"
(CONF / "contrats").mkdir(parents=True)

INSTANCE = {
    "config_version": 1,
    "client": "ACME v1",
    "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "expediteur": "rh@acme.example", "rh": ["rh@acme.example"],
              "dpae": None, "recap": None, "comptable_defaut": "c@acme.example"},
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
           formulaire=None, grille=None, comptes=COMPTES, societes=SOCIETES):
    store.db().comptes.delete_many({})
    for ident, doc in comptes.items():
        store.db().comptes.insert_one({"_id": ident, **doc})
    (CONF / "instance.json").write_text(
        json.dumps(instance or INSTANCE, ensure_ascii=False), encoding="utf-8")
    fg = CONF / "grille.json"
    fg.write_text(json.dumps(grille, ensure_ascii=False), encoding="utf-8") \
        if grille else fg.unlink(missing_ok=True)
    (CONF / "formulaire.json").write_text(
        json.dumps(formulaire or FORMULAIRE), encoding="utf-8")
    poser_societes(societes)
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


def test_comptes_absents_ou_malformes_refuses_au_demarrage():
    """La collection `comptes` est la seule source des comptes : aucun compte,
    ou un compte sans hash, refuse le démarrage au lieu de planter login()."""
    for mauvais in ({}, {"rh": {}}, {"rh": {"mdp_hash": ""}}):
        ecrire(comptes=mauvais)
        assert "comptes" in config.verifier(), f"{mauvais!r} passe le garde-fou"
    ecrire(comptes={"rh": {"mdp_hash": generate_password_hash(config.MDP_PAR_DEFAUT)}})
    assert "compte RH" in config.verifier(), "mot de passe par défaut accepté"


def test_societes_absentes_ou_malformees_refusees_au_demarrage():
    """Les sociétés vivent en base : aucune, aucune avec un établissement, ou
    un établissement sans nom refuse le démarrage. Les checks qui lisent les
    sociétés (règles, jetons) ne doivent pas planter dessus."""
    sans_nom = [{**SOCIETES[0], "etablissements": [{"siret": "1"}]}]
    mentions_nulles = [{**SOCIETES[0], "mentions": None}]
    mentions_liste = [{**SOCIETES[0], "etablissements": [
        {**SOCIETES[0]["etablissements"][0], "mentions": ["1 rue X"]}]}]
    for mauvais in ([], [{**SOCIETES[0], "etablissements": []}], sans_nom,
                    mentions_nulles, mentions_liste):
        ecrire(instance={**INSTANCE, "templates": [
            {"quand": {"etablissement": "ACME / Siège"}, "modele": "contrat.txt"}]},
            societes=mauvais)
        assert "établissements" in config.verifier(), f"{mauvais!r} passe le garde-fou"


def test_import_garde_les_places_et_ignore_l_id():
    """Une société déjà en base garde sa place, une nouvelle se range à la
    suite (et non à égalité avec la première) ; l'`_id` d'un export Mongo ne
    fait pas planter l'import."""
    ecrire()
    # ACME réexportée de la base : son `_id` diffère de celui en base, Mongo
    # refuserait de le changer (« _id immutable »).
    neuve = {**SOCIETES[0], "nom": "Neuve"}
    config.importer_societes([neuve, {**SOCIETES[0], "siren": "999", "_id": "exporte"}])
    assert [s["nom"] for s in config.societes()] == ["ACME", "Neuve"]
    places = {d["nom"]: d["ordre"] for d in store.db().societes.find()}
    assert places == {"ACME": 0, "Neuve": 1}, places
    assert config.societes()[0]["siren"] == "999", "ACME non remplacée"


def test_cache_des_societes_expire():
    """Une écriture d'un autre process (configurer.py) se voit sans redémarrer,
    au plus tard au bout de _SOCIETES_TTL."""
    ecrire()
    assert [s["nom"] for s in config.societes()] == ["ACME"]
    store.db().societes.insert_one({**SOCIETES[0], "nom": "Dehors", "ordre": 9})
    assert [s["nom"] for s in config.societes()] == ["ACME"], "relu avant le délai"
    config._societes_lu -= config._SOCIETES_TTL + 1
    assert [s["nom"] for s in config.societes()] == ["ACME", "Dehors"]


def test_id_de_champ_avec_point_ou_dollar_refuse():
    """store écrit `champs.<id>` : un « . » créerait un sous-document, un « $ »
    en tête serait lu comme un opérateur."""
    for mauvais in ("a.b", "$set"):
        form = {**FORMULAIRE, "champs": FORMULAIRE["champs"]
                + [{"id": mauvais, "libelle": "X", "type": "texte"}]}
        ecrire(formulaire=form)
        assert "champs" in config.verifier(), f"id {mauvais!r} accepté"


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


def _desactive_test_rechargement_met_a_jour_ce_qui_est_fige_a_limport():
    """Contrôle au niveau de l'APPELANT, pas de config.recharger() seul :
    recharger ne sert à rien si les valeurs lues une fois à l'import restent
    celles d'avant : les motifs de KO l'étaient."""
    ecrire()
    import app as application

    client = application.app.test_client()
    with client.session_transaction() as s:
        s["utilisateur"] = "rh"

    (CONF / "instance.json").write_text(
        json.dumps({**INSTANCE, "motifs_ko": ["Motif tout neuf"]}), encoding="utf-8")
    assert client.post("/recharger", follow_redirects=True).status_code == 200
    assert config.instance()["motifs_ko"] == ["Motif tout neuf"], \
        "motifs de KO figés à l'import"


def test_demarrer_refuse_sans_base_sans_montrer_l_uri():
    """`python app.py` sans MONGO_DB ni SECRET_KEY : refus qui nomme les
    variables manquantes, et jamais la valeur de MONGO_URI, qui porte le mot de
    passe de la base (la sortie finit dans les journaux du service)."""
    ecrire()
    env = {k: v for k, v in os.environ.items()
           if k not in ("MONGO_DB", "SECRET_KEY", "MONGOMOCK_AMORCE")}
    env.update(PYTHONIOENCODING="utf-8",
               MONGO_URI="mongodb://contratgen:mdp-de-la-base@127.0.0.1:1/")
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import app; app.demarrer()"],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    except subprocess.TimeoutExpired:
        raise AssertionError("l'app s'est mise à servir sans base configurée")
    sortie = r.stdout + r.stderr
    assert r.returncode != 0, f"l'app a démarré sans MONGO_DB :\n{sortie}"
    assert "mongo" in sortie.lower() and "MONGO_DB" in sortie and "SECRET_KEY" in sortie, sortie
    assert "mdp-de-la-base" not in sortie, f"l'URI de la base est affichée :\n{sortie}"

    # Variables complètes, serveur qui ne répond pas : le vrai pymongo lève en
    # pleine vérification -> refus lisible, toujours sans l'URI.
    env.update(MONGO_DB="acme", SECRET_KEY="k" * 40,
               MONGO_URI="mongodb://contratgen:mdp-de-la-base@127.0.0.1:1/"
                         "?serverSelectionTimeoutMS=300")
    r = subprocess.run(
        [sys.executable, "-c", "import app; app.demarrer()"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    sortie = r.stdout + r.stderr
    assert r.returncode != 0 and "base mongo illisible" in sortie, sortie
    assert "Traceback" not in sortie and "mdp-de-la-base" not in sortie, sortie
    config._CACHE.clear()


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


def _marque(**fichiers):
    """Pose un dossier marque/ dans l'instance de test et rend theme.json."""
    m = CONF / "marque"
    m.mkdir(exist_ok=True)
    for nom, octets in fichiers.items():
        (m / nom).write_bytes(octets)
    return m


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'


def _theme(t):
    """Ecrit theme.json a cote d'instance.json et vide le cache."""
    (CONF / "theme.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    config._CACHE.clear()


def test_palette_de_base_suit_base_html():
    """_PALETTE_ORIGINE recopie les couleurs de base.html pour calculer le
    contraste des couples REELS. Si la palette du design bouge et pas cette
    copie, le garde-fou valide une DA contre des couleurs qui n'existent plus
    -- en silence. Ce test est la pour que ca crie."""
    racine = Path(__file__).resolve().parent.parent / "templates" / "base.html"
    texte = racine.read_text(encoding="utf-8")
    bloc = texte[texte.index(":root"):texte.index("* { box-sizing")]
    for cle, attendu in apparence._PALETTE_ORIGINE.items():
        m = re.search(rf"--{cle}:(#[0-9a-fA-F]{{3,6}})", bloc)
        assert m, f"--{cle} introuvable dans le :root de base.html"
        trouve = m.group(1).lower()
        if len(trouve) == 4:                      # #fff -> #ffffff
            trouve = "#" + "".join(c * 2 for c in trouve[1:])
        assert trouve == attendu, (
            f"base.html dit --{cle}:{trouve}, apparence._PALETTE_ORIGINE dit {attendu} "
            "— le calcul de contraste porte sur une couleur périmée")


def test_verifier_theme_refuse_ce_qui_serait_illisible_ou_absent():
    """La DA du client. Deux familles de refus : ce qui serait illisible
    (contraste CALCULE, pas supposé) et ce qui est déclaré mais absent du
    dossier marque/ — le mode d'échec courant, le fichier que le graphiste a
    envoyé et que personne n'a copié."""
    ecrire()
    (CONF / "theme.json").unlink(missing_ok=True)
    config._CACHE.clear()
    assert "theme.json" not in config.verifier(), "fichier absent doit passer"

    # Fichier ABSENT = la DA d'origine, mais fichier CASSE = refus : un JSON
    # avale en silence, le client ouvre son app et la voit verte sans savoir
    # pourquoi. C'est la seule difference avec le patron de conservation().
    (CONF / "theme.json").write_text('{"palette": {,}', encoding="utf-8")
    config._CACHE.clear()
    manques = config.verifier()
    assert "theme.json" in manques, "un theme.json casse doit refuser de demarrer"
    assert "JSON" in " ".join(manques["theme.json"]), manques["theme.json"]

    _marque(**{"logo.svg": SVG})
    for mauvais in (
            "pas-un-objet",
            {"couleurs": {}},                                  # clé inventée
            {"palette": {"accent": "vert"}},                    # pas un hex
            {"palette": {"rose": "#c8102e"}},                   # couleur inconnue
            {"palette": {"accent": "#f2c200"}},                 # jaune : illisible
            {"palette": {"texte": "#bbbbbb"}},                  # texte trop pâle
            {"densite": 1.1},                                   # clé retirée
            {"logo": {"fichier": "../instance.json"}},          # échappée de marque/
            {"logo": {"fichier": "logo.svg", "hauteur": 200}},
            {"favicon": "absent.png"},
    ):
        _theme(mauvais)
        manques = config.verifier()                             # ne doit pas lever
        assert "theme.json" in manques, f"{mauvais!r} passe le garde-fou"

    # Le refus nomme un accent de repli, et ce repli passe vraiment.
    _theme({"palette": {"accent": "#f2c200"}})
    raison = " ".join(config.verifier()["theme.json"])
    repli = re.findall(r"#[0-9a-f]{6}", raison)[-1]
    p = {**apparence._PALETTE_ORIGINE, "accent": repli}
    assert all(apparence._contraste(a, b) >= 4.5
               for a, b in apparence._couples(p).values()),         f"la teinte suggérée {repli} ne passe pas elle-même"

    # Une DA complète et saine passe, et sort en tokens CSS.
    _marque(**{"logo.svg": SVG, "fav.svg": SVG})
    _theme({"palette": {"accent": "#c8102e", "fond": "#faf7f3"},
            "logo": {"fichier": "logo.svg", "hauteur": 30, "remplace_le_nom": True},
            "favicon": "fav.svg"})
    assert "theme.json" not in config.verifier(), config.verifier()
    da = config.apparence()
    assert da["variables"]["--accent"] == "#c8102e"
    # Les variantes sont DERIVEES, jamais demandees a l'installateur.
    assert da["variables"]["--accent-sombre"] == "#820a1e"
    assert da["logo"]["remplace_le_nom"] is True
    assert da["favicon"] == "fav.svg"

    (CONF / "theme.json").unlink()
    config._CACHE.clear()


def test_theme_atteint_la_page_et_les_fichiers_sont_servis():
    """Le thème ne sert à rien s'il ne sort pas : la DA doit être DANS la page,
    après la palette d'origine, et marque/ doit être servi — sans laisser
    remonter hors du dossier."""
    ecrire()
    _marque(**{"logo.svg": SVG})
    _theme({"palette": {"accent": "#c8102e"},
            "logo": {"fichier": "logo.svg", "remplace_le_nom": True}})

    import app as application
    client = application.app.test_client()
    page = client.get("/").get_data(as_text=True)

    assert "--accent:#c8102e" in page, "la couleur du client n'atteint pas la page"
    assert page.index("--accent:#c8102e") > page.index("--accent:#1f4d3f"),         "la DA du client est peinte avant la palette d'origine : elle perd"
    assert "/marque/logo.svg" in page and "<i>" not in page,         "le logo ne remplace pas le monogramme"

    assert client.get("/marque/logo.svg").status_code == 200
    assert client.get("/marque/absent.svg").status_code == 404
    # Flask sert marque/ : la protection de chemin est la sienne, on verifie
    # qu'on ne s'est pas trompe de dossier en la branchant.
    assert client.get("/marque/../instance.json").status_code == 404

    (CONF / "theme.json").unlink()
    config._CACHE.clear()
    assert "--accent:#c8102e" not in client.get("/").get_data(as_text=True),         "sans theme.json, la page porte encore la DA du test precedent"


def test_signer_refuse_sans_cle():
    """recap.py signe des liens hors de verifier() : sans SECRET_KEY, une cle
    vide fabriquerait des liens morts en silence. Refus net."""
    import store
    cle = os.environ.pop("SECRET_KEY")
    try:
        store.signer("lot_comptable", "u1", 0)
        raise AssertionError("lien signé avec une clé vide")
    except RuntimeError as e:
        assert "SECRET_KEY" in str(e), e
    finally:
        os.environ["SECRET_KEY"] = cle


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
