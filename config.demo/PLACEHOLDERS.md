# Placeholders — Basilic Café — Démo

Vos modèles de contrat sont des `.docx` ordinaires. Là où une valeur doit être
remplie automatiquement, écrivez le jeton correspondant **exactement** comme
dans le tableau ci-dessous.

Trois règles, sans exception :

1. **Deux accolades collées, sans espace à l'intérieur** : `{{Nom}}`, jamais
   `{{ Nom }}` — le texte est remplacé littéralement.
2. **La casse compte** : `{{Nom}}` fonctionne, `{{nom}}` non.
3. **Un jeton absent de ce tableau bloque la mise en service** : l'application
   refuse de démarrer et vous dit lequel. Rien ne part en production à moitié
   rempli.

Un jeton peut apparaître autant de fois que voulu dans le document.

## Jetons disponibles

| Jeton | Ce qu'il contient |
|---|---|
| `{{AdresseEtablissement}}` | Mention légale de l'établissement (societes.json) |
| `{{Aujourdhui}}` | Calculé automatiquement |
| `{{BlocAutorisationTravail}}` | Règle dérivée (instance.json → derives) |
| `{{Civilite}}` | Question « Civilité » du formulaire |
| `{{ConventionCollective}}` | Mention légale de la société (societes.json) |
| `{{DateDebut}}` | Question « Date de début de contrat » du formulaire |
| `{{DateEmbauche}}` | Question « Date d'embauche » du formulaire |
| `{{DateFinValidite}}` | Question « Date de fin de validité du titre » du formulaire |
| `{{DateNaissance}}` | Question « Date de naissance » du formulaire |
| `{{DateSignature}}` | Calculé automatiquement |
| `{{DispoDimanche}}` | Question « Dimanche » du formulaire |
| `{{DispoJeudi}}` | Question « Jeudi » du formulaire |
| `{{DispoLundi}}` | Question « Lundi » du formulaire |
| `{{DispoMardi}}` | Question « Mardi » du formulaire |
| `{{DispoMercredi}}` | Question « Mercredi » du formulaire |
| `{{DispoSamedi}}` | Question « Samedi » du formulaire |
| `{{DispoVendredi}}` | Question « Vendredi » du formulaire |
| `{{Domicile}}` | Question « Adresse complète » du formulaire |
| `{{DureeMensuelle}}` | Règle dérivée (instance.json → derives) |
| `{{Email}}` | Question « Email » du formulaire |
| `{{Etablissement}}` | Identité de la société / de l'établissement choisi |
| `{{FaitLe}}` | Calculé automatiquement |
| `{{FormeCapital}}` | Mention légale de la société (societes.json) |
| `{{GreffeRCS}}` | Mention légale de la société (societes.json) |
| `{{HeureDemarrage}}` | Question « Heure de démarrage » du formulaire |
| `{{Nationalite}}` | Règle dérivée (instance.json → derives) |
| `{{NationaliteEtrangere}}` | Question « Nationalité étrangère » du formulaire |
| `{{NomNaissanceUsage}}` | Calculé automatiquement |
| `{{NomPrenom}}` | Calculé automatiquement |
| `{{NumSS}}` | Question « Numéro de sécurité sociale » du formulaire |
| `{{PiecesFournies}}` | Liste des pièces jointes fournies |
| `{{PiecesManquantes}}` | Liste des pièces jointes manquantes |
| `{{Poste}}` | Question « Poste » du formulaire |
| `{{RCS}}` | Mention légale de la société (societes.json) |
| `{{RaisonSociale}}` | Mention légale de la société (societes.json) |
| `{{Representant}}` | Mention légale de la société (societes.json) |
| `{{SalaireChiffres}}` | Grille de salaires (config/grille.json) |
| `{{SalaireLettres}}` | Grille de salaires, en toutes lettres |
| `{{Semaine1}}` | Règle dérivée (instance.json → derives) |
| `{{Semaine2}}` | Règle dérivée (instance.json → derives) |
| `{{Semaine3}}` | Règle dérivée (instance.json → derives) |
| `{{Semaine4}}` | Règle dérivée (instance.json → derives) |
| `{{SiegeSocial}}` | Mention légale de la société (societes.json) |
| `{{Siren}}` | Identité de la société / de l'établissement choisi |
| `{{Siret}}` | Identité de la société / de l'établissement choisi |
| `{{Societe}}` | Identité de la société / de l'établissement choisi |
| `{{Telephone}}` | Question « Téléphone » du formulaire |
| `{{TempsTravail}}` | Question « Temps de travail » du formulaire |
| `{{TypeAutorisation}}` | Question « Type d'autorisation de travail » du formulaire |
| `{{TypeContrat}}` | Question « Type de contrat » du formulaire |
| `{{VilleSignature}}` | Mention légale de la société (societes.json) |

## Modèles attendus

- `config/contrats/Equipier.html` — déposé
- `config/contrats/Equipier_Partiel.html` — déposé
- `config/contrats/Manager.html` — déposé
