# ADR — où vivent les données : local ou dossier synchronisé

**Statut :** accepté, 2026-09-10.

## Contexte

`data/` (soumissions, dossiers salariés, pièces d'identité, RIB, contrats,
journaux, copies compta, mails console) vivait là où pointait la variable
d'environnement `DONNEES`. Rien dans l'instance ne disait *où* : le choix était
porté par la ligne de lancement, donc par celui qui tape la commande, et il
n'était vérifié nulle part. Des clients veulent leurs données sur leur drive
d'entreprise (OneDrive / SharePoint / Google Drive) plutôt que sur le disque du
serveur — pour la sauvegarde, l'historique de versions et l'accès direct depuis
l'explorateur de fichiers.

## Décision

Un bloc `stockage` dans `instance.json`, sur le modèle des blocs `signature` et
`mails` qui ont déjà un `mode` :

```json
"stockage": {"mode": "local"}
"stockage": {"mode": "dossier", "chemin": "C:/Users/rh/OneDrive - ACME/Embauches"}
```

- `local` — `data/` à côté de `config/` dans le dossier d'instance. Comportement
  historique ; c'est aussi ce que vaut un bloc absent, donc les instances déjà
  installées démarrent sans rien changer.
- `dossier` — `chemin` est la racine de `data/`. Le dossier est répliqué par le
  **client de synchronisation du fournisseur, installé sur le serveur** : l'app
  écrit sur un système de fichiers local, elle ne fait aucun appel réseau et ne
  connaît aucun fournisseur.

Le choix se pose à l'installation (`python installer.py "<Client>" <cible>
[chemin-du-drive]`), se répare avec `configurer.py --assister`, et est **vérifié
au démarrage** comme le reste de la config (`config._verifier_stockage`) : mode
connu, chemin non vide, dossier existant, et **écriture réellement testée** —
un dossier de sync pas encore répliqué, déplacé ou monté en lecture seule est
refusé au démarrage, pas découvert à la première soumission d'un vrai salarié.

Deux points d'ordre, appris en revue et couverts par un test chacun :

- `demarrer()` **vérifie avant de créer**. Le `mkdir(parents=True)` des zones
  `soumissions/` et `DOSSIERS/` s'exécute après `verifier()`, jamais avant :
  placé avant, il créait lui-même le chemin du drive annoncé, le garde-fou
  trouvait un dossier bien présent, et l'app servait en écrivant des pièces
  d'identité dans un dossier local qui *ressemble* à un dossier synchronisé.
- `installer.py` **refuse un dossier drive non vide**, comme il refuse d'écraser
  un `config/` existant : deux instances sur le même dossier fusionneraient
  leurs dossiers salariés, et `store._verrou` ne protège qu'à l'intérieur d'un
  process.

Le bloc `stockage` n'est **pas** rechargeable à chaud : `config.DONNEES` est figé
à l'import, et déplacer des données suppose de déplacer aussi les fichiers.
`/recharger` accepte la nouvelle config mais dit explicitement que les écritures
continuent d'aller à l'ancien endroit tant que l'app n'a pas redémarré — faire
semblant serait pire que ne rien faire.

`DONNEES` reste prioritaire sur le bloc : la ligne de lancement l'emporte
toujours (c'est ce dont les tests se servent, et la sortie de secours d'un
exploitant). La résolution vit dans `config._donnees()`, seul endroit qui
décide.

## Pourquoi pas d'abstraction backend

Il n'y a **pas** de classe `Stockage`, pas d'interface, pas de registre de
backends. Le seam d'I/O existe déjà, documenté en tête de `store.py` :

    chemin(uid, bucket, nom=None)      -- localiser
    ouvrir(uid, bucket, nom)           -- lire   -> octets | None
    deposer(uid, bucket, role, f)      -- écrire un upload
    poser_octets(uid, bucket, nom, o)  -- écrire des octets qu'on produit
    fichiers(item, bucket)             -- lister

Le mode `dossier` ne change **aucune** de ces cinq fonctions : c'est toujours du
système de fichiers, à une racine près. Une interface à une seule implémentation
serait du code mort aujourd'hui, et le jour où un vrai second backend arrive,
c'est ce seam-là qu'on réimplémente — pas une abstraction devinée à l'avance.

## Ce qu'un mode API exigerait (phase 2, non codé)

Un mode « API cloud directe » (Microsoft Graph, Google Drive API) se déclenche
si un client refuse d'installer un client de synchronisation sur le serveur, ou
si l'app doit tourner sur un hébergement mutualisé sans disque persistant. Il
faudrait alors :

1. Réimplémenter les cinq fonctions du seam de `store.py` contre l'API.
2. **Décider où vit le journal `dossier.json`.** C'est le point dur : il fait foi
   sur l'état, il est écrit en read-modify-write sous `store._verrou`, et ce
   verrou ne protège qu'à l'intérieur d'un process — il suppose un système de
   fichiers, pas un objet distant que deux clients peuvent écrire. Côté API il
   faut de la concurrence optimiste (ETag / `If-Match`, réessai sur 412), ou
   garder le journal en local et n'externaliser que les pièces.
3. OAuth, rafraîchissement de jeton, cache local pour les lectures répétées
   (`store.tout()` rescanne tous les `dossier.json` à chaque `/suivi`).

Voir `NOTES-infra.md`, section base de données, pour la cohérence de ce
raisonnement : le disque est la base, et le journal append-only est l'état.

## Conséquences connues du mode « dossier synchronisé »

- **Verrouillage pendant l'upload.** Le client de sync peut tenir un fichier le
  temps de le téléverser : `os.replace` et `shutil.move` (`valider()` déplace
  tout un dossier) peuvent lever `PermissionError` sous Windows. À observer sur
  un vrai dossier OneDrive avant d'ajouter un réessai — pas de parade tant que
  ce n'est pas mesuré.
- **Une seule machine écrit.** `store._verrou` est mono-process : deux serveurs
  pointés sur le même dossier drive perdraient des entrées de journal. Ce n'est
  pas un stockage partagé, c'est un disque distant.
- **Fichiers temporaires synchronisés.** Les `.tmp` / `.entrant` des écritures
  temp-puis-rename sont répliqués un instant. Cosmétique, non traité.
- **Fichiers à la demande (OneDrive).** Un fichier non téléchargé localement se
  lit quand même (le système le rapatrie) mais lentement ; à mesurer si `/suivi`
  ralentit, puisqu'il rescanne tous les `dossier.json`.
- **La sauvegarde change de forme.** Le drive porte l'historique de `data/` ;
  `tar` reste la sauvegarde de `config/`, qui lui ne quitte jamais le serveur.
- **La purge ne descend pas dans le service de synchronisation.** Le bouton
  *Purger les dossiers terminés* (bloc `conservation` d'`instance.json`) efface
  les pièces sur le système de fichiers local et vide `dossier.json` de tout
  sauf établissement / poste / date de début. En mode `dossier`, **la corbeille
  et l'historique de versions** du service (OneDrive/SharePoint/Google Drive)
  gardent la version complète d'avant purge — NIR et adresse compris, récupérables.
  C'est le trou le plus grave du mode drive parce qu'il donne l'illusion de la
  conformité : après une purge, il faut vider à la main la corbeille et purger
  l'historique de versions côté service. `python purger.py` le rappelle quand
  `stockage.mode == "dossier"`. `data/mails/*.eml` (mode console) ne portent
  aucun uid : purgés par ancienneté, pas par dossier ; en mode `smtp` ces
  données sont dans les boîtes des destinataires, hors de portée.
