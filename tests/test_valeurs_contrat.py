"""Les VALEURS imprimées dans le contrat sont les bonnes — pas seulement le
contrat « complet ».

    python tests/test_valeurs_contrat.py

Le reste de la suite vérifie qu'un contrat sort sans {{Placeholder}} orphelin.
C'est le mode d'échec visible. Le mode d'échec coûteux est l'autre : un contrat
parfaitement formé qui porte un MAUVAIS montant, une mensualisation fausse, la
clause d'une autre entité. Personne ne le relit — c'est le champ dont on doute
le moins — et il part à la signature.

Ce test construit une config cliente complète dans un dossier temporaire
(patron de test_clients.py : aucune fixture du dépôt, aucune config réelle) et
assert les chaînes exactes que la prose doit contenir. Sabotez un montant de
`GRILLE` ci-dessous : il doit virer au rouge.

Couvre, sur cette config fabriquée :
  - salaire au forfait et salaire au barème horaire, chiffres ET lettres
  - mensualisation du temps partiel (15 h/sem -> 65 h/mois) + bascule de modèle
  - bloc conditionnel étranger, et son ABSENCE pour un salarié français
  - deux entités : chacune son modèle, ses mentions, sans fuite de l'autre
  - « NOM Prénom » réordonné pour la prose
  - valeur critique vide : refus d'écrire plutôt qu'un contrat troué
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

TMP = Path(tempfile.mkdtemp(prefix="contratgen-valeurs-"))
CLIENT = TMP / "config"
(CLIENT / "contrats").mkdir(parents=True)
(CLIENT / "mails").mkdir()

# --- la config fabriquee ---------------------------------------------------
#
# Client fictif, deux societes. Les deux entites sont distinguees par leur
# ROUTAGE (quel modele leur equipier prend), jamais par leur nom : c'est la
# propriete que le moteur doit tenir chez n'importe quel client.

MISTRAL = "Boulangerie Mistral"
LEVAIN = "Levain du Sud"
ETAB_M = f"{MISTRAL} / Aix Centre"
ETAB_L = f"{LEVAIN} / Toulon Port"

# Montants pris TELS QUELS dans le contrat : aucun calcul cote moteur, donc
# aucune divergence d'arrondi avec le barème papier.
GRILLE = {
    "champ_heures": "temps_travail",
    "postes": [
        {"poste": "Manager", "mensuel": 2500},
        {"poste": "Vendeur", "bareme": {
            "15": {"chiffres": "800,15", "lettres": "huit cents euros et quinze centimes"},
            "35": {"chiffres": "1 867,02",
                   "lettres": "mille huit cent soixante-sept euros et deux centimes"},
        }},
    ],
}

SOCIETES = [
    {"nom": MISTRAL, "siren": "111 222 333", "comptable_email": "cabinet-a@example.test",
     "mentions": {"RaisonSociale": "BOULANGERIE MISTRAL SAS",
                  "SiegeSocial": "3 cours Mirabeau, 13100 Aix-en-Provence",
                  "Representant": "Claire Aubert"},
     "etablissements": [{"nom": "Aix Centre", "siret": "111 222 333 00011",
                         "mentions": {"AdresseEtablissement": "8 rue Espariat, 13100 Aix"}}]},
    {"nom": LEVAIN, "siren": "444 555 666", "comptable_email": "cabinet-b@example.test",
     "mentions": {"RaisonSociale": "LEVAIN DU SUD SARL",
                  "SiegeSocial": "12 quai Cronstadt, 83000 Toulon",
                  "Representant": "Idris Benali"},
     "etablissements": [{"nom": "Toulon Port", "siret": "444 555 666 00017",
                         "mentions": {"AdresseEtablissement": "5 place Puget, 83000 Toulon"}}]},
]

FORMULAIRE = {
    "version": 1, "titre": "Embauche boulangerie", "intro": "À remplir par le responsable.",
    "roles": {"etablissement": "boutique", "poste": "poste", "nom": "nom_complet",
              "email": "email", "date_debut": "date_debut"},
    "champs": [
        {"id": "boutique", "libelle": "Boutique", "type": "etablissement", "requis": True},
        {"id": "email", "libelle": "Email", "type": "email", "requis": True},
        # Champ unique « NOM Prenom » : le cas qui oblige a reordonner pour la prose.
        {"id": "nom_complet", "libelle": "NOM Prénom", "type": "texte", "requis": True,
         "placeholder": "NomComplet"},
        {"id": "poste", "libelle": "Poste", "type": "choix", "requis": True,
         "options": ["Manager", "Vendeur"], "placeholder": "Poste"},
        {"id": "date_debut", "libelle": "Date de début", "type": "date", "requis": True,
         "placeholder": "DateDebut"},
        {"id": "temps_partiel", "libelle": "Temps partiel", "type": "choix",
         "options": ["OUI", "NON"], "placeholder": "TempsPartiel"},
        {"id": "temps_travail", "libelle": "Heures par semaine", "type": "texte",
         "placeholder": "TempsTravail"},
        {"id": "nationalite", "libelle": "Nationalité", "type": "choix",
         "options": ["Française", "Autres"], "placeholder": "NationaliteBrute"},
        {"id": "nationalite_etrangere", "libelle": "Laquelle", "type": "texte",
         "placeholder": "NationaliteEtrangere"},
        {"id": "titre_sejour_fin", "libelle": "Titre de séjour valable jusqu'au",
         "type": "date", "placeholder": "TitreSejourFin"},
        {"id": "piece_identite", "libelle": "Pièce d'identité", "type": "piece_jointe",
         "role": "identite", "requis": True},
    ],
}

INSTANCE = {
    "client": "Boulangerie Mistral — RH",
    "secret": "0" * 64,
    "url": "https://embauche.mistral.test",
    "mails": {"mode": "console", "expediteur": "rh@mistral.test",
              "rh": ["rh@mistral.test"], "comptable_defaut": "cabinet-a@example.test"},
    "utilisateurs": {"rh": {"mdp_hash": "pbkdf2:sha256:1000$x$" + "0" * 64}},
    "motifs_ko": ["Pièce illisible"],
    # Ordre significatif : la premiere regle dont TOUS les « quand » collent gagne.
    # Les cles de « quand » sont des ID DE CHAMPS du formulaire du client (ici
    # « boutique »), pas des roles du moteur : une regle de routage appartient a
    # la config, elle parle donc le vocabulaire de ce formulaire-la.
    "templates": [
        {"quand": {"poste": "Vendeur", "temps_partiel": "OUI"}, "modele": "Vendeur_Partiel.html"},
        {"quand": {"poste": "Vendeur", "boutique": ETAB_M}, "modele": "Vendeur_Mistral.html"},
        {"quand": {"poste": "Vendeur"}, "modele": "Vendeur_Levain.html"},
        {"quand": {"poste": "Manager"}, "modele": "Manager.html"},
    ],
    "derives": [
        {"placeholder": "Nationalite", "si": ["nationalite", "==", "Autres"],
         "alors": "{{NationaliteEtrangere}}", "sinon": "française"},
        {"placeholder": "BlocTitreSejour", "si": ["nationalite", "==", "Autres"],
         "alors": "<p>Le salarié est titulaire d'un titre de séjour valable "
                  "jusqu'au {{TitreSejourFinLong}}.</p>", "sinon": ""},
        {"placeholder": "TitreSejourFinLong", "format": "date_longue", "de": "TitreSejourFin"},
        {"placeholder": "DureeMensuelle", "format": "mensualise", "de": "TempsTravail"},
    ],
    "critiques": ["SalaireChiffres", "SalaireLettres"],
}

# --- les modeles -----------------------------------------------------------
#
# Prose minimale mais REELLE : chaque assertion plus bas cite une chaine que le
# contrat doit contenir mot pour mot.

SALAIRE = ("<p>Le salarié percevra une rémunération mensuelle brute d'un montant de "
           "{{SalaireChiffres}} ({{SalaireLettres}}) euros bruts.</p>")
ENTETE = ("<p>Entre la société {{RaisonSociale}}, dont le siège est situé "
          "{{SiegeSocial}}, immatriculée sous le SIREN {{Siren}}, représentée par "
          "{{Representant}}, établissement de {{AdresseEtablissement}},</p>"
          "<p>Et Monsieur {{NomPrenom}}, né(e) de nationalité {{Nationalite}}.</p>"
          "{{BlocTitreSejour}}")

MODELES = {
    "Manager.html": ENTETE + SALAIRE
    + "<p>La période d'essai est d'une durée de trois mois.</p>"
    + "<p>CLAUSE DE NON-CONCURRENCE — applicable aux cadres.</p>",
    # Les deux entites : meme poste, meme salaire, essai different.
    "Vendeur_Mistral.html": ENTETE + SALAIRE
    + "<p>La période d'essai est d'une durée d'un mois.</p>",
    "Vendeur_Levain.html": ENTETE + SALAIRE
    + "<p>La période d'essai est d'une durée de deux mois.</p>",
    "Vendeur_Partiel.html": ENTETE + SALAIRE
    + "<p>La durée mensuelle de travail est fixée à {{DureeMensuelle}} heures par mois.</p>"
    + "<p>La période d'essai est d'une durée d'un mois.</p>",
}

(CLIENT / "instance.json").write_text(json.dumps(INSTANCE, ensure_ascii=False), encoding="utf-8")
(CLIENT / "formulaire.json").write_text(json.dumps(FORMULAIRE, ensure_ascii=False), encoding="utf-8")
(CLIENT / "societes.json").write_text(json.dumps(SOCIETES, ensure_ascii=False), encoding="utf-8")
(CLIENT / "grille.json").write_text(json.dumps(GRILLE, ensure_ascii=False), encoding="utf-8")
for nom, corps in MODELES.items():
    (CLIENT / "contrats" / nom).write_text(corps, encoding="utf-8")

# CONFIG_DIR AVANT l'import : config.py le lit au chargement du module.
os.environ["CONFIG_DIR"] = str(CLIENT)
os.environ["DONNEES"] = str(TMP / "data")

import config    # noqa: E402
import contrat   # noqa: E402


def saisie(**over):
    base = {"boutique": ETAB_M, "email": "rh@mistral.test",
            "nom_complet": "NKEMBA Awa", "poste": "Vendeur",
            "date_debut": "2026-10-12", "temps_partiel": "NON", "temps_travail": "35H",
            "nationalite": "Française", "nationalite_etrangere": "",
            "titre_sejour_fin": ""}
    base.update(over)
    return base


def rendre(champs, nom):
    """Génère le contrat pour ces champs. Renvoie (modèle choisi, texte produit).

    Le texte est aussi écrit dans TMP : quand une assertion tombe, le contrat
    fautif est sur le disque, lisible en entier."""
    modele = config.modele_pour(champs)
    assert modele, f"aucun modèle pour {champs['poste']} / {champs['temps_partiel']}"
    vals = contrat.valeurs(champs, config.mentions(config.valeur(champs, "etablissement")))
    txt = contrat.remplir(CLIENT / "contrats" / modele, vals)
    (TMP / nom).write_text(txt, encoding="utf-8")
    return modele, txt


def test_config_servable():
    """La config fabriquée doit passer le garde-fou de démarrage : sinon les
    échecs suivants viendraient d'elle, pas du moteur."""
    manques = config.verifier()
    assert not manques, manques


def test_salaire_au_forfait_et_au_bareme():
    """Chiffres ET lettres viennent de la grille, par poste. C'est l'assertion
    qui tombe si un montant de grille est faux — celle qu'aucun autre test de la
    suite ne porte."""
    _, mgr = rendre(saisie(poste="Manager"), "mgr.html")
    assert "montant de 2 500 (deux mille cinq cents) euros bruts" in mgr, mgr[-300:]

    _, vendeur = rendre(saisie(poste="Vendeur", temps_travail="35H"), "vendeur.html")
    assert ("montant de 1 867,02 (mille huit cent soixante-sept euros et deux "
            "centimes) euros bruts") in vendeur, vendeur[-300:]


def test_temps_partiel_bascule_le_modele_et_mensualise():
    """15 h/semaine -> 65 h/mois (52/12) et le barème 15H, pas celui du plein."""
    modele, txt = rendre(saisie(temps_partiel="OUI", temps_travail="15H"), "partiel.html")
    assert modele == "Vendeur_Partiel.html", modele
    assert "fixée à 65 heures par mois" in txt, "mensualisation 15H -> 65 h absente"
    assert "montant de 800,15 (huit cents euros et quinze centimes)" in txt, txt[-300:]
    assert "1 867,02" not in txt, "barème temps plein sur un contrat à temps partiel"


def test_bloc_conditionnel_present_puis_absent():
    """Le bloc étranger apparaît pour un étranger — et le contrat d'un Français
    n'en porte aucune trace. Une règle conditionnelle se teste dans les DEUX
    sens : « alors » seul laisserait passer un bloc collé à tout le monde."""
    _, etranger = rendre(saisie(nationalite="Autres", nationalite_etrangere="Marocaine",
                                titre_sejour_fin="2027-05-31"), "etranger.html")
    assert "titulaire d'un titre de séjour valable jusqu'au 31 mai 2027" in etranger, \
        "bloc conditionnel ou date longue absents"
    assert "de nationalité Marocaine" in etranger, "nationalité étrangère non reportée"

    _, francais = rendre(saisie(), "francais.html")
    assert "titre de séjour" not in francais, "bloc étranger sur un contrat français"
    assert "de nationalité française" in francais, "nationalité par défaut absente"


def test_deux_entites_chacune_son_modele_et_ses_mentions():
    """Même poste, deux sociétés : le modèle, l'essai et les mentions suivent
    l'établissement — et rien de l'autre entité ne fuit dans le contrat."""
    m_mistral, t_mistral = rendre(saisie(boutique=ETAB_M), "ent_m.html")
    m_levain, t_levain = rendre(saisie(boutique=ETAB_L), "ent_l.html")
    assert m_mistral == "Vendeur_Mistral.html", m_mistral
    assert m_levain == "Vendeur_Levain.html", m_levain
    assert "d'une durée d'un mois" in t_mistral, "essai 1 mois absent (1re entité)"
    assert "d'une durée de deux mois" in t_levain, "essai 2 mois absent (2e entité)"

    men_m, men_l = config.mentions(ETAB_M), config.mentions(ETAB_L)
    for cle in ("Representant", "SiegeSocial", "Siren", "AdresseEtablissement"):
        assert men_m[cle] in t_mistral, f"{cle} de l'entité absent de son contrat"
        assert men_l[cle] in t_levain, f"{cle} de l'entité absent de son contrat"
        assert men_l[cle] not in t_mistral, f"{cle} de l'autre entité dans le contrat"
        assert men_m[cle] not in t_levain, f"{cle} de l'autre entité dans le contrat"

    assert "NON-CONCURRENCE" not in t_mistral, "clause cadre sur un contrat de vendeur"
    _, mgr = rendre(saisie(poste="Manager"), "mgr_clause.html")
    assert "NON-CONCURRENCE" in mgr, "clause cadre absente du contrat de manager"


def test_nom_prenom_reordonne_pour_la_prose():
    """Formulaire à champ unique « NOM Prénom » : la prose dit « Monsieur Awa
    NKEMBA », jamais « Monsieur NKEMBA Awa »."""
    _, txt = rendre(saisie(nom_complet="NKEMBA Awa"), "nom.html")
    assert "Monsieur Awa NKEMBA, né(e)" in txt, "« NOM Prénom » non réordonné"
    assert "NKEMBA Awa," not in txt


def test_aucun_placeholder_orphelin():
    """Aucun {{…}} ne survit dans un contrat produit, quel que soit le couloir."""
    cas = [("Manager", saisie(poste="Manager")),
           ("Vendeur plein", saisie()),
           ("Vendeur partiel", saisie(temps_partiel="OUI", temps_travail="15H")),
           ("Vendeur étranger", saisie(nationalite="Autres",
                                       nationalite_etrangere="Marocaine",
                                       titre_sejour_fin="2027-05-31"))]
    for libelle, champs in cas:
        _, txt = rendre(champs, f"orphelin-{libelle}.html")
        assert "{{" not in txt, f"{libelle} : contrat troué"


def test_valeur_critique_vide_refuse_le_contrat():
    """Poste hors grille -> salaire vide. Sans garde, le contrat sortirait avec
    « un montant de  () euros bruts » : complet, sans aucun {{}} à repérer, et
    faux. La garde doit lever AVANT d'écrire."""
    champs = saisie(poste="Vendeur", temps_travail="12H")   # 12H : hors barème
    vals = contrat.valeurs(champs, config.mentions(ETAB_M))
    try:
        contrat.remplir(CLIENT / "contrats" / "Vendeur_Levain.html", vals)
        assert False, "contrat rempli malgré un salaire vide"
    except ValueError as e:
        assert "Salaire" in str(e), e


def main():
    test_config_servable()
    test_salaire_au_forfait_et_au_bareme()
    test_temps_partiel_bascule_le_modele_et_mensualise()
    test_bloc_conditionnel_present_puis_absent()
    test_deux_entites_chacune_son_modele_et_ses_mentions()
    test_nom_prenom_reordonne_pour_la_prose()
    test_aucun_placeholder_orphelin()
    test_valeur_critique_vide_refuse_le_contrat()
    print(f"Valeurs OK — grille, mensualisation, blocs conditionnels, 2 entités\n{TMP}")


if __name__ == "__main__":
    main()
