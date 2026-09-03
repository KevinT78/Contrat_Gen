"""La solution générique appliquée au vrai cas Wingstop.

    python tests/test_wingstop.py

Prend les 4 templates HTML réels (déposés dans _wingstop_reel/, recopiés dans
config_wingstop/contrats/) et le formulaire réel (config_wingstop/), et prouve
que le moteur générique remplit chaque contrat sans placeholder orphelin —
sans toucher ni aux {{Placeholders}} des modèles ni aux champs du formulaire :

  - mapping champ -> placeholder            (config/formulaire.json)
  - grille de salaires par poste            (config_wingstop/grille.json)
  - formateur mensualisation                (15H -> 65 h/mois)
  - règles dérivées                         (bloc titre de séjour conditionnel)
  - saisie RH                               (planning du temps partiel)
  - sélection de template par règle         (temps partiel bascule le modèle,
                                             comparaison casse/accents-insensible)
  - garde-fou valeur critique vide          (contrat troué -> refus)

Le parcours RH complet servi sur cette config est couvert par
tests/test_parcours_wingstop.py.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RACINE = Path(__file__).resolve().parent.parent
os.environ["CONFIG_DIR"] = str(RACINE / "config_wingstop")
sys.path.insert(0, str(RACINE))

import config          # noqa: E402
import contrat         # noqa: E402

CONTRATS = config.CLIENT / "contrats"
TMP = Path(tempfile.mkdtemp(prefix="wingstop-"))

# Ce que la RH saisit à la génération : le planning du temps partiel (le
# salaire, lui, vient désormais de la grille).
RH = {"Semaine1": "35", "Semaine2": "35", "Semaine3": "35", "Semaine4": "35",
      "ReposConsecutifs": "Oui", "ReposFractionnes": "Non"}


def champs(**over):
    c = {
        "etablissement": "Wing Kitchen / Boulogne (DK)",
        "poste": "Equipier Polyvalent", "civilite": "Monsieur",
        "nom_prenom": "DUPONT Jean", "date_naissance": "1997-06-12",
        "telephone": "0600000000", "email": "jean@example.com",
        "adresse": "5 rue des Fleurs, 92100 Boulogne", "num_secu": "1 97 06 92 042 123 45",
        "date_embauche": "2026-09-15", "type_contrat": "CDI",
        "date_debut": "2026-10-01", "heure_demarrage": "9h",
        "nationalite": "Français", "nationalite_etrangere": "",
        "type_autorisation": "", "date_fin_validite": "",
        "temps_partiel": "NON", "temps_travail": "35H",
        "lundi": "", "mardi": "", "mercredi": "", "jeudi": "",
        "vendredi": "", "samedi": "", "dimanche": "",
    }
    c.update(over)
    return c


def rendre(ch, nom):
    vals = contrat.valeurs(ch, config.mentions(ch["etablissement"]), extra=RH)
    modele = config.modele_pour(ch)
    assert modele, f"aucun modèle pour poste={ch['poste']} partiel={ch['temps_partiel']}"
    dest = contrat.generer(CONTRATS / modele, vals, TMP / nom)
    return modele, dest.read_text(encoding="utf-8")


def test_config_wingstop_servable():
    m = config.verifier()
    assert not m, m


def test_salaire_vient_de_la_grille_par_poste():
    # forfait : Manager -> 2 500 ; Assistant Manager -> 2 000
    _, txt = rendre(champs(poste="Manager"), "manager.html")
    assert "montant de 2 500 (deux mille cinq cents) euros bruts" in txt, txt[-400:]
    _, txt = rendre(champs(poste="Assistant Manager"), "am.html")
    assert "montant de 2 000 (deux mille) euros bruts" in txt
    # SMIC barématisé : Équipier 35H -> ligne "35" de la grille, prise telle quelle
    _, txt = rendre(champs(poste="Equipier Polyvalent"), "eq35.html")
    assert "1 867,02 (mille huit cent soixante-sept euros et deux centimes)" in txt


def test_chaque_poste_un_contrat_plein():
    for poste in ("Equipier Polyvalent", "Assistant Manager", "Manager"):
        modele, txt = rendre(champs(poste=poste), f"{poste}.html")
        assert "{{" not in txt, f"{modele} : contrat troué"
        assert "de nationalité française" in txt, f"{modele} : nationalité non dérivée"
        assert "titre de séjour" not in txt, f"{modele} : bloc étranger sur un Français"


def test_prenom_nom_reordonne_pour_la_prose():
    _, txt = rendre(champs(nom_prenom="NKEMBA Awa"), "reorder.html")
    assert "Monsieur Awa NKEMBA, né(e)" in txt, "« NOM Prénom » non réordonné"
    assert "NKEMBA Awa," not in txt


def test_temps_partiel_bascule_le_template_et_mensualise():
    # casse volontairement bruitée : "equipier polyvalent" / "Oui"
    ch = champs(poste="equipier polyvalent", temps_partiel="Oui", temps_travail="15H")
    modele, txt = rendre(ch, "partiel.html")
    assert modele == "Equipier_Partiel.html", modele
    assert "{{" not in txt, "contrat partiel troué"
    assert "65 heures par mois" in txt, "mensualisation 15H -> 65 h absente"
    assert "800,15 (huit cents euros et quinze centimes)" in txt, "salaire barème 15H absent"
    assert "<td>Oui</td>" in txt and "<td>Non</td>" in txt, "saisie RH repos absente"


def test_salarie_etranger_ajoute_le_bloc_titre_de_sejour():
    ch = champs(nationalite="Autres", nationalite_etrangere="Marocaine",
                type_autorisation="Carte de séjour", date_fin_validite="2027-05-31")
    modele, txt = rendre(ch, "etranger.html")
    assert "{{" not in txt, f"{modele} : contrat troué"
    assert "titre de séjour de type « Carte de séjour »" in txt, "bloc conditionnel absent"
    assert "valable jusqu'au 31 mai 2027" in txt, "date de validité non formatée"
    assert "de nationalité Marocaine" in txt, "nationalité étrangère non reportée"


def test_valeur_critique_vide_refuse_le_contrat():
    """NomPrenom vide -> le contrat aurait « Monsieur , né(e)… » sans aucun {{}}
    à détecter. La garde doit lever avant écriture."""
    vals = contrat.valeurs(champs(nom_prenom=""), config.mentions(champs()["etablissement"]),
                           extra=RH)
    try:
        contrat.generer(CONTRATS / "Equipier_Polyvalent.html", vals, TMP / "vide.html")
        assert False, "contrat écrit malgré NomPrenom vide"
    except ValueError as e:
        assert "NomPrenom" in str(e), e


def main():
    test_config_wingstop_servable()
    test_salaire_vient_de_la_grille_par_poste()
    test_chaque_poste_un_contrat_plein()
    test_prenom_nom_reordonne_pour_la_prose()
    test_temps_partiel_bascule_le_template_et_mensualise()
    test_salarie_etranger_ajoute_le_bloc_titre_de_sejour()
    test_valeur_critique_vide_refuse_le_contrat()
    print(f"Wingstop OK — grille de salaires + 4 templates réels, 0 placeholder orphelin\n{TMP}")


if __name__ == "__main__":
    main()
