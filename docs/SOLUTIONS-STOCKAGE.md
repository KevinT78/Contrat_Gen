# Solutions de stockage des données — comparatif

Où peuvent vivre les données de Contrat_Gen (journal `dossier.json`, pièces
d'identité, contrats, copies compta), et à quel prix. Ce document compare les
solutions déjà en place, celles évoquées dans les guides sans être codées, et
d'autres pistes jamais discutées. Il ne remplace pas [ADR-stockage.md](ADR-stockage.md)
(le raisonnement derrière le design actuel) ni les guides pas-à-pas.

Contrainte commune à toutes les solutions : l'app est **mono-process** (waitress,
verrou `store._verrou` intra-process). Toute solution qui suppose plusieurs
serveurs actifs sur les mêmes données est hors périmètre.

Rappel de ce que le code sait faire aujourd'hui :

- Bloc `stockage` dans `config/instance.json`, deux modes : `local` et `dossier`.
- `installer.py` choisit le mode à l'installation (3e argument = chemin du dossier
  synchronisé → mode `dossier`, sinon `local`).
- `config._verifier_stockage()` vérifie au démarrage : mode connu, chemin présent,
  dossier existant, **écriture réellement testée**.
- `store.py` isole un seam de 5 fonctions pour les pièces (`chemin`, `ouvrir`,
  `deposer`, `poser_octets`, `fichiers`). Trois autres endroits touchent aussi
  les pièces hors du seam : `valider()` (déplacement du dossier et renommage des
  pièces en noms lisibles), `copier_compta()`, `_effacer_pieces()` (purge).

---

## Vue d'ensemble

| # | Solution | RH voit « MARTIN Camille » dans SharePoint | Machine | Code | État |
|---|---|---|---|---|---|
| 1 | `local` | non | n'importe laquelle | aucun | livré, défaut |
| 2 | `dossier` (client de sync sur la machine) | oui, en direct | Windows | aucun | livré |
| 3 | `local` + `rclone copy` nocturne | oui, avec un jour de retard, lecture seule | Linux ou Windows | aucun (cron) | à installer, pas de code |
| 4 | Partage réseau SMB/NFS + `dossier` | oui si le NAS synchronise | n'importe laquelle + NAS | aucun | à tester |
| 5 | Montage FUSE OneDrive + `dossier` | oui, en direct | Linux | aucun | à tester, risqué |
| 6 | Graph API, Forme A (pièces via API, journal local) | oui, en direct | Linux ou Windows | moyen | plan écrit, non codé |
| 7 | Graph API, Forme B (tout via API) | oui, en direct | Linux ou Windows | lourd | non codé, déconseillé |
| 8 | WebDAV (Nextcloud, ou SharePoint) | oui si Nextcloud | Linux ou Windows | moyen | non codé |
| 9 | Stockage objet S3 | non | Linux ou Windows | moyen | non codé |
| 10 | Google Drive API | oui, dans Drive | Linux ou Windows | moyen | non codé |

---

## 1. `local` — disque de la machine

**Ce que c'est.** `data/` à côté de `config/`, sur le disque de la machine qui
sert l'app. `DONNEES=` sur la ligne de lancement l'emporte toujours.

**Avantages**
- Aucun prérequis, aucun réseau. Renommage atomique fiable (`os.replace`).
- Le plus rapide, le plus simple à diagnostiquer.

**Inconvénients**
- La sauvegarde est entièrement à la charge de l'exploitant.
- La RH n'a aucun accès direct aux dossiers : tout passe par l'app.
- Les pièces d'identité vivent sur le disque du serveur.

**Quand le choisir.** VPS Linux, ou toute installation où personne n'a besoin
d'ouvrir les dossiers à la main. À combiner avec la solution 3 pour la sauvegarde.

**Changements de code.** Aucun.

---

## 2. `dossier` — dossier répliqué par un client de synchronisation

**Ce que c'est.** `data/` = un chemin situé dans un dossier que le client
OneDrive / SharePoint / Google Drive installé **sur la machine** réplique. L'app
écrit sur un système de fichiers local et ne connaît aucun fournisseur.

**Avantages**
- Arbo lisible dans SharePoint et l'explorateur, en direct.
- SharePoint porte l'historique et la corbeille.
- Zéro code, zéro dépendance, zéro appel réseau dans l'app.
- Une seule arbo : rien à réconcilier.

**Inconvénients**
- Le client de sync doit tourner sur la machine : en pratique Windows.
- Une suppression faite à la main dans SharePoint est répercutée localement ;
  la corbeille du fournisseur est alors la seule sauvegarde (voir solution 3
  comme filet).
- Fichier ouvert dans l'Explorateur ou en cours d'upload OneDrive : renommage
  refusé (`WinError 32`). La purge est écrite pour encaisser ce cas et rejouer.

**Quand le choisir.** Dès qu'une machine Windows est disponible : poste du
bureau, VPS Windows, ou serveur de fichiers de l'entreprise. C'est le scénario
prévu par le design et la réponse la moins chère à « la RH doit voir les noms
en direct ».

**Changements de code.** Aucun. Pas-à-pas dans
[GUIDE-STOCKAGE-SHAREPOINT.md](GUIDE-STOCKAGE-SHAREPOINT.md).

---

## 3. `local` + copie `rclone` nocturne

**Ce que c'est.** Mode `local` sur le serveur, plus un cron qui pousse une
copie de `data/` et `config/` vers un drive, en une seule direction, sans jamais
monter le drive comme stockage vivant.

```
rclone copy /srv/acme/data onedrive-acme:sauvegardes/contrat-gen/data --backup-dir onedrive-acme:sauvegardes/contrat-gen/historique/$(date +%F)
```

**Avantages**
- Arbo lisible sur SharePoint, historique daté par `--backup-dir`.
- Le stockage vivant reste un vrai disque : atomicité garantie.
- Zéro code.

**Inconvénients**
- Miroir en lecture seule : une modification faite dans SharePoint n'est pas
  reprise par l'app, et sera écrasée à la copie suivante.
- Décalage d'une nuit. Ce n'est pas du « direct ».

**Quand le choisir.** VPS Linux, quand la RH n'a pas besoin de manipuler les
dossiers dans SharePoint mais veut les y consulter et que l'historique doit
sortir du serveur. Recommandé sur VPS Linux. Aussi utile **en complément** de
la solution 2, comme sauvegarde indépendante de la corbeille SharePoint.

**Changements de code.** Aucun.

---

## 4. Partage réseau SMB/NFS + mode `dossier`

**Ce que c'est.** Un partage d'un NAS ou d'un serveur de fichiers monté sur la
machine qui sert l'app ; le bloc `stockage` pointe dessus en mode `dossier`. Le
NAS peut lui-même synchroniser vers SharePoint.

**Avantages**
- Zéro code, fonctionne sous Linux comme sous Windows.
- Le renommage atomique est fiable sur SMB, contrairement à FUSE.
- La RH ouvre les dossiers depuis le partage, comme un lecteur réseau.

**Inconvénients**
- Suppose une infrastructure déjà là (NAS, serveur de fichiers).
- Partage indisponible = app en panne. La sonde d'écriture au démarrage le
  détecte, pas une coupure en cours de service.
- Le lien vers SharePoint dépend du NAS, pas de l'app.

**Quand le choisir.** Entreprise qui a déjà un serveur de fichiers ou un NAS
avec synchronisation cloud, et un serveur applicatif Linux.

**Changements de code.** Aucun. À tester une fois sur une instance de test :
`valider()` et la purge, partage monté.

---

## 5. Montage FUSE OneDrive + mode `dossier`

**Ce que c'est.** Sur un VPS Linux, monter le drive avec `rclone mount` ou
`abraunegg/onedrive`, puis pointer le mode `dossier` dessus.

**Avantages**
- SharePoint en direct depuis Linux, zéro code.

**Inconvénients**
- `os.replace` et le déplacement de dossier ne sont pas garantis atomiques sur
  FUSE. `valider()` déplace un dossier entier et la purge renomme : un échec à
  mi-chemin laisse un dossier incohérent.
- Cache local, latence, comportement variable selon l'outil et sa version.
- Non testé.

**Quand le choisir.** Tentative bon marché à faire **avant** de coder la
solution 6, sur une instance de test, en rejouant soumission, validation, dépôt
de contrat et purge sous charge. À abandonner au premier doute.

**Changements de code.** Aucun.

---

## 6. Graph API, Forme A — pièces via l'API, journal sur le disque

**Ce que c'est.** Un troisième mode `graph` du bloc `stockage`. Les pièces
(`FICHE PERSONNELLE/`, `CONTRAT/`, `_versions/`, copies compta) sont lues et
écrites sur un drive SharePoint via Microsoft Graph, en auth app-only
(`client_credentials`, permission `Sites.Selected`). Le journal, la machine à
états, le verrou et les mails restent sur le disque du serveur. L'arbo
SharePoint **reflète l'arbo locale** : le chemin distant d'une pièce est le
chemin local du dossier relatif à `DONNEES`, donc la RH voit
`DOSSIERS SALARIES/<Groupe>/<Établissement>/<Poste>/MARTIN Camille/`.

Un plan détaillé existe (`~/.claude/plans/stockage-graph-forme-a.md`, 2026-09-10).
Il a été écrit avec une arbo plate par ULID ; la décision « noms lisibles »
prise le 2026-09-16 le modifie sur les points listés ci-dessous.

**Avantages**
- SharePoint en direct depuis un VPS Linux, arbo lisible.
- Les pièces d'identité ne sont pas sur le disque du VPS.
- Rétro-compatibilité totale : bloc absent, `local` ou `dossier` inchangés.

**Inconvénients**
- Deux arbos (squelette local avec le journal, pièces distantes) à garder
  alignées sans transaction. Chaque déplacement ou renommage local doit avoir
  son jumeau Graph, et un échec entre les deux laisse une pièce introuvable.
- La RH qui renomme un dossier à la main dans SharePoint casse l'adressage.
  Il faut une commande de réconciliation qui compare les deux arbos.
- Application Azure à enregistrer, octroi `Sites.Selected` par un admin,
  secret à faire tourner dans `instance.json`.
- Nouvelle dépendance `msal`. Le serveur doit sortir en HTTPS vers
  `graph.microsoft.com` et `login.microsoftonline.com`.
- Un appel API par ligne de `/suivi` pour la colonne « pièces manquantes »
  sans cache.

**Quand le choisir.** VPS Linux imposé **et** accès direct de la RH imposé,
après que les solutions 3, 4 et 5 ont été écartées. C'est le seul cas qui
justifie d'écrire du code.

**Changements de code.**

| Fichier | Changement |
|---|---|
| `config.py` | mode `graph` du bloc `stockage` (`tenant_id`, `client_id`, `secret`, `site_id`, `drive_id`, `racine`) ; mode figé à l'import comme `DONNEES` ; `_verifier_stockage()` fait une vraie écriture Graph au démarrage ; `_donnees()` inchangé (le journal reste local) |
| `graph.py` (nouveau, racine) | jeton app-only via `msal` ; HTTP en `urllib.request` avec un `_TRANSPORT` injectable sur le modèle de `signature.py` ; les 5 fonctions du seam, mêmes signatures ; session d'upload au-delà de 4 Mo ; primitives `deplacer(chemin, vers)` et `renommer(chemin, nom)` |
| `store.py` | 5 gardes d'une ligne dans le seam (`if _backend_graph(): return graph.<fn>(...)`) ; `copier_compta()` ; `_effacer_pieces()` ; dans `valider()`, déplacement Graph du dossier **avant** le `shutil.move` local, renommage Graph des pièces avant chaque `rename` local ; dans la purge, renommage Graph en `purge-<uid>` avant le `rename` local. Règle : geste distant d'abord, geste local ensuite, une erreur Graph laisse l'état rejouable |
| `store.tout()` / `fichiers()` | cache d'inventaire amorcé au chargement pour que `/suivi` ne fasse pas un appel par ligne |
| `reconcilier.py` (nouveau) ou sous-commande de `doctor.py` | compare l'arbo locale et l'arbo Graph, liste les écarts, propose de renommer côté Graph pour réaligner |
| `installer.py` | drapeau `--stockage graph` qui écrit le bloc avec les champs vides ; le démarrage refuse tant qu'ils ne sont pas remplis ; message de fin qui renvoie vers `configurer.py --assister` |
| `configurer.py` | option 3 « Microsoft Graph » dans `_reparer_stockage`, cinq `input()` puis écriture du bloc |
| `requirements.txt` | `msal>=1.28`, importé paresseusement dans `graph._jeton()` |
| `tests/test_stockage_graph.py` (nouveau) | faux drive en mémoire derrière `_TRANSPORT` ; couvrir soumission, validation (déplacement + renommage), purge, copie compta, et l'échec Graph à mi-validation |
| `docs/GUIDE-VPS.md` | corriger la phrase « rien d'autre ne construit un chemin de pièce » (faux pour `valider()`, `copier_compta()`, la purge) |

Hors code : enregistrement de l'application Azure, octroi de la permission sur
le site par un admin, fourniture des cinq identifiants.

---

## 7. Graph API, Forme B — tout via l'API, journal compris

**Ce que c'est.** Comme la Forme A, mais `dossier.json` et `soumission.json`
vivent aussi sur SharePoint.

**Avantages**
- Plus rien sur le serveur. Plusieurs serveurs actifs deviennent envisageables.

**Inconvénients**
- Réécriture du cœur de `store.py` : le journal est écrit en lire-modifier-réécrire
  sous un verrou intra-process qui suppose un système de fichiers. Il faut de la
  concurrence optimiste (ETag, `If-Match`, réessai sur 412) et un cache avec
  invalidation pour `/suivi`.
- Chaque changement d'état devient un aller-retour réseau.
- Une RH qui édite `dossier.json` à la main dans SharePoint corrompt l'état.

**Quand le choisir.** Seulement sur un besoin explicite : plusieurs serveurs,
ou interdiction totale de données sur le serveur, journal compris. Déconseillé.

**Changements de code.** Tout de la Forme A, plus la réécriture de `_ecrire`,
`lire`, `tout`, `_verrou` et de l'amorçage de `_carte` contre Graph.

---

## 8. WebDAV — Nextcloud, ou SharePoint

**Ce que c'est.** Même découpage que la Forme A (pièces à distance, journal
local), mais le protocole est WebDAV : `PUT`, `GET`, `MOVE`, `PROPFIND`.

**Avantages**
- Protocole simple, `urllib` suffit, aucune dépendance nouvelle.
- `MOVE` couvre déplacement et renommage en une requête.
- Excellent avec Nextcloud, qui donne aussi une arbo lisible et un client web.

**Inconvénients**
- Côté SharePoint moderne, l'authentification WebDAV est fragile et souvent
  désactivée par les tenants : à vérifier chez le client avant de s'engager.
- Mêmes problèmes de double arbo et de réconciliation que la Forme A.

**Quand le choisir.** Client qui utilise Nextcloud ou un serveur WebDAV plutôt
que SharePoint.

**Changements de code.** Ceux de la Forme A, avec `webdav.py` à la place de
`graph.py` et sans `msal` (auth basique ou jeton d'application). Le bloc
`stockage` prend `url`, `utilisateur`, `mot_de_passe`, `racine`.

---

## 9. Stockage objet — S3, Backblaze B2, Scaleway, OVH

**Ce que c'est.** Pièces adressées par clé (`<chemin relatif>/<bucket>/<nom>`)
dans un bucket objet, journal local.

**Avantages**
- Versioning et chiffrement natifs, durabilité élevée, coût faible.
- Pas de notion de dossier : un renommage est une copie plus une suppression,
  mais rien n'est jamais « à moitié déplacé » du point de vue d'une clé.

**Inconvénients**
- La RH n'y ouvre rien à la main, et ça ne parle pas à SharePoint.
- Dépendance `boto3`, ou signature SigV4 à écrire à la main.
- Déplacement d'un dossier entier à la validation = une copie par pièce.

**Quand le choisir.** Quand l'objectif est de sortir les pièces du serveur, pas
de les montrer à la RH. Inutile si le besoin est « voir les noms dans SharePoint ».

**Changements de code.** Ceux de la Forme A, avec `s3.py` à la place de `graph.py`.

---

## 10. Google Drive API

**Ce que c'est.** Symétrique de la Forme A pour un client sous Google Workspace :
compte de service, API Drive, dossier partagé.

**Avantages / inconvénients.** Ceux de la Forme A, avec le SDK Google à la
place de `msal`. Les fichiers Drive sont adressés par identifiant et non par
chemin, donc le renommage d'un dossier par la RH ne casse rien, mais il faut
alors stocker les identifiants dans le journal.

**Quand le choisir.** Uniquement si un client sous Workspace l'impose. Sinon la
solution 2 avec le client Google Drive installé couvre déjà le besoin sans code.

**Changements de code.** Ceux de la Forme A, plus une table `id` Drive par
pièce dans `dossier.json`.

---

## Comment choisir

1. **La RH doit-elle voir les pièces dans SharePoint en direct ?**
   Non : solution 1 + 3. Oui : question suivante.
2. **Peut-on avoir une machine Windows, ou un partage réseau ?**
   Poste du bureau, VPS Windows, NAS : solution 2 (ou 4), zéro code. Non :
   question suivante.
3. **VPS Linux imposé et accès direct imposé.**
   Essayer la solution 5 sur une instance de test. Si elle échoue, solution 6
   avec arbo miroir, en acceptant la réconciliation entre deux arbos.

Les solutions 7 à 10 ne répondent qu'à des contraintes particulières
(multi-serveurs, Nextcloud, pas de SharePoint, Workspace) et ne sont pas des
candidates par défaut.
