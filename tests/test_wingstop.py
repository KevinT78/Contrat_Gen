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
  - planning hebdo repris des heures        (24H -> 4 semaines à 24H)
  - repos du temps partiel                  (2 questions du formulaire, requis_si)
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
CLIENT = RACINE / "config_wingstop"
# La config du client n'est pas versionnee : un clone frais ne l'a pas, et ce
# test n'a alors rien a prouver. Sortie 0 -- un rouge ici doit vouloir dire
# « le moteur a casse », jamais « le depot ne porte pas les donnees du client ».
if not (CLIENT / "instance.json").exists():
    print("config_wingstop/ absent — test ignoré : il tourne sur la config "
          "réelle du client, qui ne vit pas dans le dépôt.")
    raise SystemExit(0)
os.environ["CONFIG_DIR"] = str(CLIENT)
sys.path.insert(0, str(RACINE))

import config          # noqa: E402
import contrat         # noqa: E402

CONTRATS = config.CLIENT / "contrats"
TMP = Path(tempfile.mkdtemp(prefix="wingstop-"))

# Raisons sociales, SIREN, communes et noms de dirigeants appartiennent au
# client : ils sont LUS dans config/, jamais ecrits ici -- ce fichier part sur
# GitHub, la config non.
DEFAUT = config.etablissements()[0][0]


def etab_vers(modele, **over):
    """Cle de l'etablissement dont ce poste tire `modele`. Deriver la cle du
    routage plutot que l'ecrire en dur : c'est la regle qu'on veut prouver."""
    for cle, _ in config.etablissements():
        if config.modele_pour(champs(etablissement=cle, **over)) == modele:
            return cle
    raise AssertionError(f"aucun établissement ne route vers {modele}")


def champs(**over):
    c = {
        "etablissement": DEFAUT,
        "poste": "Equipier Polyvalent", "civilite": "Monsieur",
        "nom_prenom": "DUPONT Jean", "date_naissance": "1997-06-12",
        "telephone": "0600000000", "email": "jean@example.com",
        "adresse": "5 rue des Fleurs, 92100 Villeneuve", "num_secu": "1 97 06 92 042 123 45",
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
    # Ce test asserte sur la PROSE remplie (substitution, roles, grille, derives,
    # bascule de template) -- pas sur le .docx final, couvert par
    # test_parcours_wingstop. On lit donc le HTML rempli via contrat.remplir.
    vals = contrat.valeurs(ch, config.mentions(ch["etablissement"]))
    modele = config.modele_pour(ch)
    assert modele, f"aucun modèle pour poste={ch['poste']} partiel={ch['temps_partiel']}"
    return modele, contrat.remplir(CONTRATS / modele, vals)


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
    # les 2 lignes de repos restent au contrat, cellule vide à compléter à la main
    assert "2 jours de repos consécutifs par semaine</td><td></td>" in txt, "ligne repos absente/non vide"
    # le tableau des semaines est désormais rempli depuis les heures du formulaire
    assert "<td>15H</td><td>15H</td><td>15H</td><td>15H</td>" in txt, "planning hebdo non repris"


def test_salarie_etranger_ajoute_le_bloc_titre_de_sejour():
    ch = champs(nationalite="Autres", nationalite_etrangere="Marocaine",
                type_autorisation="Carte de séjour", date_fin_validite="2027-05-31")
    modele, txt = rendre(ch, "etranger.html")
    assert "{{" not in txt, f"{modele} : contrat troué"
    assert "titre de séjour de type « Carte de séjour »" in txt, "bloc conditionnel absent"
    assert "valable jusqu'au 31 mai 2027" in txt, "date de validité non formatée"
    assert "de nationalité Marocaine" in txt, "nationalité étrangère non reportée"


def test_deux_entites_et_equipier_route_par_etablissement():
    """Les établissements relèvent de deux sociétés distinctes : l'équipier temps
    plein de la 1re entité a son modèle propre (essai 1 mois), celui de la 2e
    prend sa version (essai 2 mois). Aucun des deux n'a de non-concurrence.

    Les deux entités sont retrouvées PAR LEUR ROUTAGE, pas par leur nom :
    l'identité du client vit dans config/, qui n'est pas versionnée."""
    cle_bl = etab_vers("Equipier_Polyvalent.html", poste="Equipier Polyvalent")
    cle_wk = etab_vers("Equipier_Polyvalent_WK.html", poste="Equipier Polyvalent")
    assert cle_bl != cle_wk, "les deux entités ne sont pas distinguées"

    m_bl, t_bl = rendre(champs(etablissement=cle_bl, poste="Equipier Polyvalent"), "eq_bl.html")
    m_wk, t_wk = rendre(champs(etablissement=cle_wk, poste="Equipier Polyvalent"), "eq_wk.html")
    assert m_bl == "Equipier_Polyvalent.html", m_bl
    assert m_wk == "Equipier_Polyvalent_WK.html", m_wk

    assert "d'une durée d'un mois" in t_bl, "essai 1 mois absent (1re entité)"
    assert "d'une durée de deux mois" in t_wk, "essai 2 mois absent (2e entité)"
    # chaque contrat porte les mentions de SON entite -- et aucune de l'autre :
    # c'est la moitie « pas de fuite » que la matrice attendu/vu doit couvrir.
    men_bl, men_wk = config.mentions(cle_bl), config.mentions(cle_wk)
    for m in ("Representant", "SiegeSocial", "Siren"):
        assert men_bl[m] in t_bl, f"{m} de l'entité absent de son contrat"
        assert men_wk[m] in t_wk, f"{m} de l'entité absent de son contrat"
    # Le « pas de fuite d'une entite vers l'autre » n'est verifie que sur le
    # siege et le SIREN : les 5 modeles du client nomment un dirigeant EN DUR
    # dans la clause « responsable de traitement » au lieu de {{Representant}},
    # donc un contrat de la 1re entite porte aujourd'hui le representant de
    # l'autre a cet endroit. Defaut de balisage cote config, pas du moteur --
    # a rouvrir ici des que les modeles seront corriges.
    for m in ("SiegeSocial", "Siren"):
        assert men_wk[m] not in t_bl, f"{m} de l'autre entité dans le contrat"
        assert men_bl[m] not in t_wk, f"{m} de l'autre entité dans le contrat"
    assert men_wk["AdresseEtablissement"] in t_wk, "adresse d'établissement non reportée"
    for t in (t_bl, t_wk):
        assert "NON-CONCURRENCE" not in t and "DEBAUCHAGE" not in t, "équipier : clause en trop"

    # les postes cadres gardent la non-concurrence, dans les deux entités
    _, t_mgr = rendre(champs(etablissement=cle_wk, poste="Manager"), "mgr.html")
    assert "NON-CONCURRENCE" in t_mgr and men_wk["Societe"] in t_mgr


def test_valeur_critique_vide_refuse_le_contrat():
    """NomPrenom vide -> le contrat aurait « Monsieur , né(e)… » sans aucun {{}}
    à détecter. La garde doit lever avant écriture."""
    vals = contrat.valeurs(champs(nom_prenom=""), config.mentions(champs()["etablissement"]))
    try:
        contrat.remplir(CONTRATS / "Equipier_Polyvalent.html", vals)
        assert False, "contrat rempli malgré NomPrenom vide"
    except ValueError as e:
        assert "NomPrenom" in str(e), e


def main():
    test_config_wingstop_servable()
    test_salaire_vient_de_la_grille_par_poste()
    test_chaque_poste_un_contrat_plein()
    test_prenom_nom_reordonne_pour_la_prose()
    test_temps_partiel_bascule_le_template_et_mensualise()
    test_salarie_etranger_ajoute_le_bloc_titre_de_sejour()
    test_deux_entites_et_equipier_route_par_etablissement()
    test_valeur_critique_vide_refuse_le_contrat()
    print(f"Wingstop OK — grille de salaires + 4 templates réels, 0 placeholder orphelin\n{TMP}")


if __name__ == "__main__":
    main()
