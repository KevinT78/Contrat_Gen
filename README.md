# Contrat_Gen — produit RH auto-hébergé

Parcours complet **formulaire → validation → dossier salarié → contrat →
signature → DPAE → remise au cabinet comptable**, sur MongoDB et sans
Office365. Implémente les décisions de `.scratch/produit-rh/` et le schéma
Excalidraw (deux couloirs : Dark Kitchen / Restaurant).

**Un client = une copie de ce dépôt**, avec sa config dans `config/`. Rien n'est
mutualisé entre clients.

Le `config/` versionné ici est une **fixture de démonstration**, pas un
déploiement : il ne porte aucun secret (les secrets sont des variables
d'environnement) et ses identifiants de test (`rh` / `fixture`) sont publics
volontairement. Un déploiement réel se pose hors du dépôt avec `installer.py`
(voir plus bas), qui génère son propre `SECRET_KEY` et son propre mot de passe RH.

```bash
pip install -r requirements.txt -r requirements-dev.txt   # requirements-dev : mongomock, base en mémoire des tests
pip install -r requirements-sharepoint.txt   # seulement si instance.json a un bloc « sharepoint »
python tests/test_excel.py        # « Compléter les dossiers » importés depuis les Excel du logiciel RH et du planning
python tests/test_parcours.py     # les deux couloirs, de bout en bout
python tests/test_signature.py    # e-sign Yousign contre un transport factice
python tests/test_clients.py      # 2 clients factices, chacun dans son instance
python tests/test_produit.py      # garde-fou de balisage, fiche, config versionnée
python demo.py                    # http://localhost:5000  (rh / demo-rh), base en mémoire
```

## Installer chez un nouveau client

Le **code est le produit**, l'**instance** est un dossier à part qui ne contient
que ce qui appartient au client. `config.exemple/` est le squelette versionné :
une instance ne se copie jamais depuis celle d'un autre.

```bash
export MONGO_URI="mongodb+srv://…" MONGO_DB=acme      # la base NEUVE de ce client
python installer.py "ACME Restauration" /srv/acme
```

`installer.py` crée le compte `rh` **en base** (mot de passe affiché une seule
fois), **copie** `config.exemple/` vers `/srv/acme/config/` et écrit
`/srv/acme/.env` (lignes `CLE=valeur`, mode 600) : `SECRET_KEY` généré,
`MONGO_URI`, `MONGO_DB`, `CONFIG_DIR`. Aucune valeur d'un autre client ne peut
survivre par oubli — c'est structurel, pas une liste de champs à nettoyer. Il
**refuse** si `config/` ou `.env` existe déjà, ou si la base a déjà un compte
`rh` (c'est la base d'un autre client : `MONGO_DB` oublié dans le shell).

### Où vivent les données : MongoDB

Dossiers, pièces, contrats, comptes, sociétés : tout est dans MongoDB, une base
par client, rien sur le disque du serveur (collections détaillées plus bas,
[docs/ADR-stockage.md](docs/ADR-stockage.md) pour le raisonnement). Le disque ne
garde que `config/`, en lecture seule pour l'app.

Les secrets ne sont jamais dans `config/` ni en base, seulement dans
l'environnement :

| Variable | Quand |
|---|---|
| `SECRET_KEY` | toujours : session et signature des liens |
| `MONGO_URI`, `MONGO_DB` | toujours (pas de valeur par défaut : une base par client) |
| `SMTP_MOT_DE_PASSE` | `mails.mode = smtp` avec un `utilisateur` |
| `YOUSIGN_CLE_API` | `signature.mode = yousign` |

Chaque commande prend le `.env` de l'instance avec l'option `--env`, sous Windows
comme sur le VPS :

```powershell
python configurer.py --env "C:\srv\acme\.env"
python app.py --env "C:\srv\acme\.env"
```

Chaque ligne `CLE=valeur` est coupée au premier `=` ; les espaces autour et une paire de
guillemets englobants sont retirés. Une ligne illisible arrête la commande en donnant son numéro.
Une variable déjà posée dans l'environnement l'emporte sur le fichier : la ligne
`EnvironmentFile=` de systemd fonctionne donc toujours, mais n'est plus nécessaire.

Il faut ensuite remplir `config/` puis démarrer. **Le démarrage refuse de servir**
tant que l'installation est incomplète : variable d'environnement manquante (le
message nomme la variable, jamais la valeur de `MONGO_URI`, qui porte le mot de
passe de la base), compte RH au mot de passe par défaut, aucun établissement
déclaré, `mails.rh` / `mails.expediteur` vides, ou `url` (adresse publique,
base des liens de tous les mails) vide (`config.verifier()`). Une base
injoignable (`ping`) est refusée de la même façon. Sans destinataire RH, chaque
demande partirait en `mail_echoue` dans le journal sans que personne ne le voie.

`configurer.py` rend ce garde-fou lisible **avant** de lancer le serveur, et
enchaîne `placeholders` puis `doctor` dès que la config passe :

```bash
python configurer.py             # bilan : chaque manque, et le fichier à ouvrir
python configurer.py --assister  # pose les questions (mails, comptes, société), boucle jusqu'à OK
python configurer.py compte marc # ajoute ou remplace un compte (en base)
python configurer.py importer-societes societes.json  # charge des sociétés et établissements en base
python configurer.py mail vous@acme.fr  # envoi de test en SMTP, même en mode console
```

L'assistant n'écrit que ce qui se dicte : adresses, comptes (en base), société et
établissements (en base). Rôles, balisage des modèles et grille restent à éditer
à la main ; il montre où, puis revérifie.

Quand la config passe, le bilan signale en `⚠`, **sans bloquer**, ce qui est
légitime en démo mais faux chez un client : `mails.mode` resté en `console`,
établissement sans `manager_email`, société sans cabinet comptable, `url` hors
https, pas de bloc `conservation`.

Le démarrage refuse aussi un barème de grille incomplet : chaque durée
hebdomadaire proposée par le formulaire doit avoir sa ligne, sinon ces salariés
auraient un contrat sans salaire (`doctor` n'essaie que la première durée).

Un compte par personne qui valide : `installer.py` ne crée que `rh`, les autres
s'ajoutent avec `python configurer.py compte <ident>` (mot de passe demandé, hash
écrit dans la collection `comptes`).

Tous les comptes ont les mêmes droits ; le journal de chaque dossier porte qui a
fait quoi.
Un `SECRET_KEY` faible rend les liens du lot comptable forgeables, un mot de passe
par défaut ouvre des pièces d'identité — c'est une frontière de confiance.

`config_version` dans `instance.json` fixe le format de la config. Le code
avançant séparément des instances déjà installées, une config d'une autre
version majeure refuse de démarrer en disant quoi faire — l'app ne réécrit
**jamais** la config du client dans son dos.

### Derrière un reverse proxy — `PROXIES`

Les deux rate-limits (formulaire public et `/login`) comptent par IP. Derrière un
proxy, `request.remote_addr` vaut l'IP du proxy pour tout le monde : il faut lire
`X-Forwarded-For`. Mais cet en-tête n'est digne de confiance **que** s'il est posé
par un proxy à nous — exposé en direct, c'est le client qui l'écrit, et le faire
tourner contourne entièrement les deux plafonds.

`PROXIES` déclare donc le nombre **réel** de proxies devant l'app :

| Valeur | Quand |
|---|---|
| `0` (défaut) | `python app.py` exposé en direct — `X-Forwarded-For` est ignoré |
| `1` | derrière Caddy / nginx |
| `2` | un CDN ajouté devant Caddy |

```bash
PROXIES=1 python app.py          # avec le .env de l'instance chargé
```

Trop haut : l'effet dépend du proxy. nginx avec `$proxy_add_x_forwarded_for`
ajoute à la valeur du client, et `remote_addr` redevient forgeable. Caddy écrase
cette valeur : l'app retombe alors sur l'IP du proxy, comme un réglage trop bas.
Trop bas : tous les visiteurs partagent un compteur unique et la Nᵉ requête
légitime est jetée en silence.

waitress efface `X-Forwarded-*` par défaut avant l'app : `demarrer()` ne le laisse
passer que si `PROXIES` est posé. `tests/test_login.py` couvre les deux réglages,
dont un cas sur le vrai `python app.py` (le client de test Flask contourne
waitress et ne voit pas ce piège).

**Le cookie de session est `Secure` dès que `DEBUG` n'est pas posé** : un navigateur
refusera de le renvoyer en clair. Servir la prod en `http://` sans terminaison TLS
donne donc un login qui « ne fait rien » — sans message d'erreur, la page revient
simplement déconnectée. C'est voulu (l'app sert des pièces d'identité), et c'est
Caddy qui fournit le TLS. En local, `DEBUG=1` lève la contrainte.

### Faire tourner, mettre à jour, sauvegarder

Un client = un serveur. Trois choses à savoir, et elles découlent toutes de la
même règle : **le code est le produit, l'instance est ailleurs.**

**Lancer.** `python app.py` démarre *waitress*, un serveur WSGI de production —
pas le serveur de développement. Waitress est mono-process multi-thread par
construction, et c'est voulu : les plafonds de rate-limit sont en mémoire et
restent exacts avec un seul process. Plusieurs process ne perdent plus de
données (le journal s'écrit par compare-and-swap en base), mais chaque plafond
est alors multiplié par le nombre de process.

`DEBUG=1 python app.py` bascule sur le serveur Werkzeug + son debugger : local
uniquement, l'app sert des pièces d'identité.

L'écoute est sur `127.0.0.1` par défaut : l'app se sert **derrière** un reverse
proxy qui porte le TLS, elle ne s'expose pas elle-même. `HOST=0.0.0.0` seulement
quand le proxy est ailleurs (autre conteneur, autre machine) — jamais pour ouvrir
sur l'extérieur en clair.

**Mettre à jour.** Rien à migrer, parce que rien de ce qui appartient au client
ne vit dans le dépôt :

| Dossier | Qui le possède | À la mise à jour |
|---|---|---|
| le code (`*.py`, `templates/`) | le produit | **remplacé** |
| `$CONFIG_DIR` (défaut `config/`) | le client | **jamais touché** — l'app n'écrit jamais sa config |
| la base MongoDB (`MONGO_DB`) | le client | **jamais touchée** |

Donc : remplacer le code, relancer. Au démarrage, `config.verifier()` refuse de
servir si la nouvelle version réclame quelque chose que la config n'a pas — un
placeholder sans source, un rôle manquant, un établissement absent — et dit quoi
corriger, plutôt que de démarrer à moitié. Si le **format** de config a changé de
version majeure, `config_version` fait refuser le démarrage avec la marche à
suivre : l'app ne réécrit jamais la config du client dans son dos.

**Sauvegarder.** La base **est** les données : dossiers, pièces d'identité,
RIB, contrats générés et signés, comptes, sociétés. `config/` contient le
paramétrage, `.env` les secrets. Trois choses à sauvegarder, rien d'autre :

```bash
mongodump --uri="$MONGO_URI" --db="$MONGO_DB" --archive=sauvegarde-$(date +%F).gz --gzip
tar czf config-$(date +%F).tar.gz /srv/acme/config /srv/acme/.env
```

(ou la sauvegarde gérée de l'hébergeur de la base). Restaurer = `mongorestore`
puis relancer. Les sauvegardes gardent les données jusqu'à leur propre
expiration, y compris celles qu'une purge a effacées de la base vive.

### Les modèles de contrat, c'est le client qui les balise

Le client renvoie ses `.docx` avec les `{{Placeholders}}` **déjà en place**. La
liste des jetons valides dépend de sa config, donc elle se génère :

```bash
CONFIG_DIR=/srv/acme/config python placeholders.py   # -> config/PLACEHOLDERS.md
```

C'est le document à lui envoyer, et à régénérer après toute modification de sa
config. `config.verifier()` refuse au **démarrage** un jeton mal écrit —
`{{ Nom }}`, `{{nom}}` — et dit lequel écrire à la place. La faute de frappe se
paie à la mise en service, jamais à la génération du contrat d'un vrai salarié.

La **relecture juridique** du modèle balisé reste au client : le garde-fou
vérifie qu'un placeholder est alimenté, pas qu'il est au bon endroit.

`config.verifier()` refuse aussi une règle `templates` (forme liste) dont un
`quand` ne peut **jamais** correspondre à un dossier réel — champ inconnu,
option mal orthographiée, ou établissement écrit sans sa société (`"Wagram"`
au lieu de `"Ailes & Cie / Wagram"`, la clé rendue par `config.etablissements()`).
Sans ce garde-fou la règle passe le bilan et le mauvais modèle part en
silence, absorbé par la règle fourre-tout suivante.

### `doctor` — relire les contrats que la config produit vraiment

```bash
CONFIG_DIR=/srv/acme/config python doctor.py
```

Un salarié fictif est promené sur chaque croisement que la config sait
distinguer — poste × temps partiel × durée hebdo × … × établissement — et le
contrat est **réellement généré** dans un dossier temporaire :

```
Couverture de « ACME » — 16 cas, contrats dans …/contrat-gen-doctor

  Couvreur      / CDI / 24H / Chantier Nord   ✓ 01-Couvreur-CDI-24H-Chantier-Nord.docx
  Chef d'équipe / CDI / 24H / Chantier Nord   ✗ aucun modèle ne vise ce cas (instance.json → templates)
  Apprenti      / CDI / 24H / Chantier Sud    ⚠ valeurs vides dans CDI.docx : SalaireChiffres
  Manager       / CDI / 24H / Chantier Sud    – exclu (volontaire)
  Couvreur      / CDI / 24H / Entrepôt        – contrat déposé (fait hors de l'app)
```

C'est le livrable de la relecture juridique : le client relit **ses** contrats,
pas un modèle abstrait. Les colonnes sortent de ses propres règles `templates`
(les clés de `quand`), du champ dont dépend le barème (`grille.json` →
`champ_heures`) et des champs que lisent les règles `derives`, donc un client
qui branche sur le temps partiel voit ses deux cas, et un salaire lu sur la
durée hebdo n'est plus figé sur une seule valeur d'exemple. Un cas
volontairement sans contrat (`"modele": null` dans une règle `templates`)
sort en « exclu (volontaire) », hors compte comme un contrat déposé — pas un
échec. Sortie non nulle dès qu'un cas ne produit pas son contrat alors qu'il
le devrait — à rejouer après chaque modification de `config/`.

Rien n'est écrit hors du dossier temporaire : la commande est sûre sur une
instance en production. Elle attrape ce que `config.verifier()` ne peut pas
voir — une durée hebdomadaire hors du barème de `grille.json`, par exemple,
sort ici en `⚠` (rémunération vide) au lieu d'exploser devant la RH le jour de
l'embauche.

### Un changement plus tard

Salaire revalorisé, clause modifiée : éditer les `.json` de `config/`, puis
redémarrer l'application — `config/` est relu au démarrage. Une config
invalide empêche le démarrage : l'application dit ce qui cloche, et
`python doctor.py` permet de la vérifier avant.

Les **sociétés et établissements** ne sont pas dans `config/` : ils vivent en
base. Un nouvel établissement s'ajoute à l'écran « Établissements » (RH), ou par
`python configurer.py --assister`, ou en rechargeant un fichier (rejouable : une
société du fichier remplace celle de même nom, les autres restent) :
`python configurer.py importer-societes societes.json`. Forme du fichier
(`config/societes.amorce.json` en est un exemple) :

```json
[
  {"nom": "ACME Restauration", "siren": "111 222 333",
   "comptable_email": "compta@acme.example",
   "mentions": {"RaisonSociale": "...", "FormeCapital": "...", "RCS": "...",
                "SiegeSocial": "...", "ConventionCollective": "..."},
   "etablissements": [
     {"nom": "Bastille", "siret": "111 222 333 00011", "contrat": "genere",
      "manager_email": "bastille@acme.example",
      "mentions": {"AdresseEtablissement": "..."}}]}
]
```

`manager_email` est l'adresse **fixe** du manager de l'établissement : le mail de
rejet (lien de correction) et l'avis de validation y partent, quel que soit l'email
tapé dans le formulaire ; sans elle, ils partent à la RH (`mails.rh`), jamais à l'email
saisi, qui est celui du candidat.

### Qui reçoit quoi

Expéditeur unique : `mails.expediteur`.

| Quand | Modèle | Destinataire |
|---|---|---|
| Soumission ou correction du formulaire | `nouvelle_soumission` | `mails.rh` |
| Rejet par la RH | `rejet` | `manager_email` de l'établissement, repli `mails.rh` |
| Validation par la RH | `demande_validee` | `manager_email` de l'établissement, repli `mails.rh` |
| Validation par la RH | `rappel_dpae` | `mails.dpae`, repli `mails.rh` |
| Cron hebdomadaire (`recap.py`) | `recap_hebdo` | le cabinet de chaque société, `mails.rh` en copie |

Le **multi-société** *à l'intérieur* d'un client reste possible :
la fixture `config/societes.amorce.json` contient « ACME Restauration » et « ACME Sud »,
deux SIREN du même client, chacun avec ses établissements et son cabinet. Le
multi-tenant (une copie servant plusieurs entreprises) est hors périmètre —
pièces d'identité et RIB, une fuite inter-client serait un incident RGPD.

## Les deux couloirs

Chaque établissement porte `"contrat"` (en base, avec sa société) :

| Valeur | Couloir | À l'état `ATraiter` |
|---|---|---|
| `genere` (défaut) | Dark Kitchen | **Générer le contrat** — `.docx` rempli depuis le template du poste |
| `depose` | Restaurant | **Déposer le contrat** — fait à la main dans le logiciel RH, PDF ou `.docx` |

Les deux voies mènent à `ContratPret`, d'où la DPAE se déclare directement.

## Signature

- **`"signature": {"mode": "manuel"}`** (défaut) : la RH télécharge le contrat, le
  fait signer hors app, dépose le PDF signé. `"mode": "yousign"` ajoute « Envoyer à
  la signature » au même endroit que le dépôt manuel.
- Le contrat signé **ne bloque pas** la DPAE ni la remise, mais reste à recueillir. Il se
  dépose une seule fois, **uniquement en `RemisComptable`**, depuis la fiche salarié
  (vue « Salariés ») où son bloc reste en haut tant qu'il manque ; noté au journal
  (`contrat_signe`) sans changer d'état ; pas en `Parti` ni après une purge. Tant
  qu'il manque, un badge **« signé manquant »** le rappelle dans « Salariés ». Il
  reste côté RH : le lot du cabinet ne le contient pas
  (`store.fichiers_compta`).
- L'ancienne clé `signature.avant_dpae` (et l'état `ContratSigne`) n'existe plus :
  `doctor` la signale comme obsolète sans bloquer. Les dossiers déjà écrits à
  `ContratSigne` restent lisibles et passent à la DPAE.

## La démo en 5 minutes

`python demo.py` crée une base **en mémoire** (mongomock, vide à chaque
lancement), amorce quatre dossiers à des étapes
différentes (demande reçue, demande validée en contrat déposé, contrat prêt,
remis au cabinet) sur la config de démo fictive versionnée (`config.demo/`,
« Basilic Café ») et sert sur `:5000` — identifiants `rh` / `demo-rh`.
`python demo.py --vide` démarre sur un suivi vide pour tout jouer à la main. La
base de la démo vit dans le process : `recap.py` lancé à part ne la voit pas ;
pour rejouer le mail hebdo, poser un `MONGO_URI` réel avant `demo.py`.
Le déroulé ci-dessous vaut aussi pour la fixture `config/` (les tests y créent
le compte `rh` / `fixture`).

1. `http://localhost:5000` — le formulaire, **rendu depuis `config/formulaire.json`**.
   Remplir, joindre 3 fichiers PDF/JPG.
2. `http://localhost:5000/login` — `rh` / `fixture`. Le suivi liste soumissions
   et dossiers, triés par date de début, retards en rouge.
3. Ouvrir la demande → **Rejeter** avec un motif → mail de KO avec **lien signé de
   correction** dans la console : l'ouvrir, corriger. L'ancien lien est mort.
4. **Accepter** → dossier salarié (même ULID, changement de zone) ; la **fiche
   salarié** est toujours générée dans `FICHE PERSONNELLE/` — au modèle du
   client (`fiche_salarie` dans `instance.json`) s'il en déclare un, sinon au
   modèle générique versé avec le code (`modeles/fiche_salarie.docx`) — et le
   **rappel DPAE** part aussitôt à `mails.dpae`
   (repli `rh`) avec nom, poste, date de début, employeur et SIRET. Établissement
   en contrat « genere » : le contrat est produit dans la foulée (bouton
   **Générer** seulement si la config réclame une saisie RH, ou pour reprendre
   après un échec). Établissement en contrat « depose » : **Déposer le contrat**.
5. **DPAE** : dépôt de l'accusé (refusé sans pièce), dès le contrat prêt. Le
   **contrat signé** se dépose plus tard, depuis la fiche salarié, sans changer d'état.
6. **Remettre au comptable** → le dossier passe à `RemisComptable`. Aucune copie
   n'est faite : le lot du cabinet (`/lot/<jeton>`) sert les pièces en direct
   depuis la base, avec des noms de fichiers lisibles, et un re-dépôt après
   remise apparaît dans le lot. Aucun mail à ce moment : le cabinet
   **de la société concernée** reçoit le lien signé dans le mail hebdomadaire
   (`recap.py`) ; la page sans login offre « Tout télécharger (.zip) ».
   **Refaire le lien** → l'ancien lien renvoie 410, le dossier repart
   dans le prochain mail hebdo.

Les mails ne partent pas : `"mode": "console"` les écrit dans la collection `mails` de la base.
Passer à `"smtp"` pour de vrais envois ; la variable d'env `MAILS_MODE` surclasse
le fichier. Dès qu'un `mails.utilisateur` est renseigné, STARTTLS vérifié + login
sont imposés ; sans identifiant, l'envoi part en clair (catcher local type MailHog).

**Tester les mails localement** : `lancer_test_emails.bat` lance MailHog (SMTP
`localhost:1025`, UI `http://localhost:8025`) et l'app avec `MAILS_MODE=smtp` —
`config/instance.json` n'est pas modifié. MailHog s'installe via
`scoop install mailhog` ou depuis les *releases* GitHub `mailhog/MailHog`.

## Mail hebdomadaire au cabinet comptable

```bash
python recap.py            # dossiers remis depuis le dernier envoi réussi
python recap.py --jours 14 # fenêtre du tout premier envoi d'un cabinet (défaut 7)
```

**Un mail par cabinet** (`comptable_email` de la société, repli
`mails.comptable_defaut`), la RH en copie (`mails.recap`, repli `mails.rh`) : les
dossiers remis au comptable depuis le dernier envoi réussi à ce cabinet (journal
`vers == RemisComptable`, ou un renvoi), chacun avec son **lien de lot signé**
(30 jours). Un envoi raté ne fait rien avancer : ses dossiers repartent au
passage suivant, et l'écran « Salariés » l'affiche en rouge
(collection `recap`, écrite par `recap.py` seul). Un cabinet ne voit
jamais les dossiers d'une autre société ; aucun mail à un cabinet sans dossier.
Les liens sont fabriqués hors requête : `instance.json → url` (adresse publique
de l'app) est obligatoire, le démarrage le refuse vide. Aucun scheduler dans
l'app — à mettre en cron / Tâche planifiée côté client, par ex. :

```cron
0 8 * * 1  cd /srv/AcmeRH && /srv/AcmeRH/venv/bin/python recap.py
```

## Ce qui varie par client — en donnée, jamais en code

Le paramétrage vit dans `config/` — aucun `.py` n'y entre jamais. (Les sociétés
et établissements sont en base : SIREN, mentions, cabinet, SIRET, couloir `contrat`.)

| Fichier | Ce qu'il porte |
|---|---|
| `instance.json` | `config_version`, nom, signature, fiche salarié, SMTP (sans mot de passe), destinataires, motifs de KO, poste → template. Ni secrets ni comptes : les secrets sont des variables d'environnement, les comptes sont en base |
| `formulaire.json` | les champs du formulaire, leur type, le placeholder `.docx` de chacun, et la table `roles` |
| `grille.json` | grille de rémunération (facultative : sans elle, le salaire est saisi au formulaire) |
| `contrats/*.docx` | modèles de contrat +, si le client en dépose un, `fiche_salarie.docx` — à placeholders `{{Nom}}` |
| `mails/*.txt` | objet + corps de chaque mail |

Au démarrage, `config.verifier()` extrait les `{{placeholders}}` des modèles
actifs (contrats, fiche salarié déclarée par le client ; le modèle générique
retire lui-même les lignes sans valeur) et refuse ceux qu'aucune source n'alimente — le garde-fou qui rend
l'adaptation à un nouveau client vérifiable.

Il exige aussi, quand un `grille.json` est présent, **une ligne de grille par
poste du formulaire**. La grille reconnaît un poste par mots normalisés et la
ligne la plus spécifique gagne : cette souplesse est voulue (« Équipier
Polyvalent » prend le tarif « Équipier » quand le client n'a pas fait plus fin),
mais un poste *privé* de sa ligne tomberait alors en silence sur celle d'un
poste dont l'intitulé est contenu dans le sien — « Apprenti couvreur » payé au
tarif « Couvreur ». Le contrat n'est pas vide, il est **faux**, donc invisible
pour `doctor` comme pour la RH. Deux postes qui visent la même ligne sont donc
refusés au démarrage ; si le partage est voulu, deux lignes de même montant le
disent.

### Les rôles : comment le moteur lit un formulaire qu'il ne connaît pas

Le moteur ne lit **aucun id de champ**. Il ne connaît qu'un vocabulaire fermé de
huit rôles (`config.ROLES`) et la table `roles` de `formulaire.json` fait le
pont. Le client nomme ses champs comme il veut :

```json
"roles": {"etablissement": "chantier", "poste": "fonction",
          "nom": "identite_nom", "email": "courriel"},
"champs": [
  {"id": "chantier",         "libelle": "Sur quel chantier ?", "type": "etablissement"},
  {"id": "numero_carte_btp", "libelle": "N° carte BTP", "type": "texte",
                             "placeholder": "CarteBTP"}
]
```

`chantier` joue le rôle `etablissement` : le moteur route les mentions légales,
le cabinet comptable et le couloir de contrat dessus. `numero_carte_btp` n'a
aucun rôle : le moteur l'ignore, il ne va qu'au template. C'est la moitié
« champ libre » du formulaire, et elle n'a pas de limite.

Quatre rôles sont **requis** (`etablissement`, `poste`, `nom`, `email`) — sans
eux le parcours ne peut pas tourner. Les autres sont facultatifs : un formulaire
à champ unique « NOM Prénom » n'a ni prénom ni nom d'usage, et `config.identite()`
s'en accommode. Une table `roles` mal écrite — rôle inconnu, champ inexistant,
rôle requis absent — refuse le **démarrage** en disant lequel.

Sans déclaration, chaque rôle retombe sur son id historique (`etablissement`,
`poste`, `nom_naissance`…) : une instance existante n'a rien à changer.

## MongoDB est la base

Toutes les données du client sont dans une base MongoDB (une base par client,
`MONGO_DB`), rien sur le disque du serveur :

| Collection | Contenu |
|---|---|
| `dossiers` | un document par soumission ou dossier salarié, **journal compris** |
| `fichiers` | pièces et contrats, octets inline (15 Mo maximum par fichier), versions comprises |
| `recap` | état du mail hebdomadaire par cabinet |
| `mails` | mode console seulement : le `.eml` en base |
| `comptes` | identifiant + hash du mot de passe |
| `societes` | sociétés, établissements imbriqués |
| `etat` | jeton SharePoint |

Le disque ne garde que `config/` (paramétrage, lecture seule pour l'app). Un
document `dossiers` porte un **journal append-only** qui fait foi sur l'état ;
il s'écrit par compare-and-swap, donc deux écritures simultanées ne se perdent
pas. 8 états :
`Soumise → Rejetee / ATraiter → ContratPret →
DpaeFaite → RemisComptable → Parti`, plus `Abandonnee` (`ContratSigne` ne subsiste
que dans `TRANSITIONS`, pour les dossiers écrits avant la suppression de l'étape). Le rappel DPAE n'est pas un
état : c'est un effet de la validation, tracé dans le journal seulement s'il
échoue (`mail_echoue`). Les transitions permises sont
dans `store.TRANSITIONS` — un `POST` hors séquence est refusé côté serveur, pas
seulement caché dans le template. `Parti` est terminal (aucune transition
sortante) : le départ d'un salarié (bouton « Archiver dans les anciens salariés », onglet
« Anciens salariés » de `/salaries`) ne déplace rien : le dossier reste où il
est et prend l'état `Parti`, avec la date de sortie au journal.

**Plusieurs process.** Plus de perte de données : le journal s'écrit en base par
compare-and-swap. Il reste que les plafonds de rate-limit (formulaire public,
`/login`) sont en mémoire, donc multipliés par le nombre de process ; waitress
mono-process reste le lancement recommandé.

**Compression des pièces.** Toute pièce stockée (formulaire, dépôt RH, import
SharePoint) passe par `compression.py` : une photo devient un `.jpg` de 2000 px
au plus, un PDF voit ses images recompressées (son texte n'est jamais touché).
Clé optionnelle `"compression": "gris"` d'`instance.json` : niveaux de gris,
1600 px, un peu plus léger ; absente = `"couleur"`. Fichier abîmé, format
inconnu ou résultat plus lourd : l'original est gardé. Mesuré sur le SharePoint
Wingstop : -44 % en couleur, -49 % en gris (photos -80 à -90 %, PDF -36 à -41 %).

**Conservation et purge.** Bloc optionnel `conservation` d'`instance.json` :

```json
"conservation": {"jours": 1095, "jours_candidature": 730,
                 "apres": ["RemisComptable", "Rejetee", "Abandonnee"]}
```

Ajouter `"Parti"` à `apres` purge aussi les anciens salariés N jours après leur
archivage (le décompte part du clic d'archivage, pas de la date de sortie
saisie) ; non activé par défaut. Un dossier dont l'état courant est dans `apres` depuis plus de `jours` (ou
`jours_candidature` si l'état est `Soumise`) devient éligible à la purge :
`/suivi` l'affiche, le bouton *Purger les dossiers terminés* (RH) efface les
pièces en base (CNI, RIB, contrats, versions comprises), réduit les champs du
dossier à établissement / poste / date de début, nettoie le journal (commentaires libres,
IP, procédures Yousign jetés) et y garde une entrée `purge`.
**Aucun octet supprimé sans trace : le journal reste.** Bloc
absent = aucune purge (rétro-compatible). La purge s'exécute dans une requête,
jamais dans un cron. `python purger.py` est le pendant
**lecture seule** : liste les éligibles (code de sortie non nul s'il en reste)
et rappelle que la collection `mails` (mode console) n'est pas purgée par dossier.
Les **sauvegardes de la base** gardent les données jusqu'à leur propre
expiration : à régler côté hébergeur.

**Direction artistique du client.** Fichier optionnel `config/theme.json`, plus
un dossier `config/marque/` pour le logo et le favicon — servi par Flask sur
`/marque/`. Absent = la DA d'origine.

```json
{
  "palette": {"accent": "#c8102e", "fond": "#faf7f3"},
  "logo":    {"fichier": "logo.svg", "hauteur": 30, "remplace_le_nom": true},
  "favicon": "favicon.svg"
}
```

Le client ne rédige **jamais de CSS** : il pose des valeurs, l'app écrit la
feuille. Une CSS fournie ne se validerait pas (on ne calcule pas le contraste
d'un fichier qu'on ne comprend pas) et casserait en silence à chaque refonte de
`base.html`.

- `palette` — au choix `accent`, `fond`, `surface`, `texte`, `bord`. **Cinq
  couleurs à poser, onze de posées** : les variantes de l'accent (survol,
  teinte pâle) et les secondaires (`--gris`, `--gris-clair`, `--bord-fort`,
  `--surface-2`, qui portent les libellés, les soulignements de champ et les
  cartes) sont **dérivées** de celles-là. Le design les avait déjà construites
  comme des mélanges — les fractions utilisées reproduisent les valeurs réglées
  à la main à 1-4 unités RGB près. Sans ça, une DA violette gardait des
  libellés kaki.
  Les couleurs d'état (alerte, attente, terminé) ne suivent pas l'accent :
  elles sont sémantiques, une alerte reste rouge chez un client bleu. Elles ne
  sont pas dérivées non plus — vérifié, ce sont de vraies teintes réglées à la
  main (écart de 6 à 18 unités avec un mélange), les dériver les désaturerait.
- `logo` — remplace le monogramme ; `hauteur` de 16 à 40px (l'en-tête fait
  60px), `remplace_le_nom` évite d'afficher le nom deux fois quand le logo est
  un logotype.

Volontairement limité aux couleurs et à la marque : polices, échelle des
textes, densité et rayons demandaient de mettre toute la feuille de style en
`calc()`, pour un gain que le client ne réclame pas. À rouvrir s'il le demande.

Tout est refusé **au démarrage** : contraste calculé sur les couples texte/fond
réels de `base.html` (il faut 4,5:1 partout — le message nomme un accent
assombri qui passe), valeurs hors bornes, clés inconnues, et **fichier déclaré
mais absent de `marque/`** — le mode d'échec courant n'est pas la faute
de frappe, c'est le logo que personne n'a copié dans l'instance.

## Fichiers

| | |
|---|---|
| `app.py` | routes et parcours ; aucune action au `GET` |
| `store.py` | accès à MongoDB (dossiers, pièces), journal, états + `TRANSITIONS`, liens signés HMAC |
| `contrat.py` | remplissage des `.docx` (`paragraphes`/`remplacer` repris du générateur historique, hors dépôt) |
| `config.py` | lecture de `config/` + garde-fou de démarrage |
| `apparence.py` | `theme.json` traduit en tokens CSS : palette, dérivées, contraste, logo ; ne connaît pas `config` |
| `mails.py` | rendu des templates + SMTP ou console |
| `signature.py` | e-sign Yousign, optionnel, désactivé par défaut |
| `installer.py` | crée l'instance d'un nouveau client : compte `rh` en base, `config/` depuis `config.exemple/`, `.env` des secrets |
| `placeholders.py` | fiche des `{{Jetons}}` à remettre au client, dérivée de sa config |
| `recap.py` | mail hebdomadaire au cabinet : dossiers remis + liens de lot (CLI, à mettre en cron) |
| `sharepoint.py` | bouton « Récupérer les dossiers » : import des salariés déjà en poste depuis un SharePoint (Microsoft Graph, connexion par code) ; actif seulement avec un bloc `sharepoint` dans `instance.json` — `requirements-sharepoint.txt` |
| `excel.py` | bouton « Compléter les dossiers » : lecture des exports `.xlsx` de la RH (stdlib), valeurs proposées puis enregistrées par la RH |
| `purger.py` | pendant lecture seule de la purge : éligibles, orphelins, rappel sur la collection `mails` (CLI) |
| `doctor.py` | couverture de la config, vérifiée en produisant les contrats |
| `configurer.py` | bilan de config lisible (avec avertissements de mise en production), assistant interactif, ajout de comptes, import de sociétés, envoi de test SMTP |
| `tests/test_parcours.py` | les deux couloirs, signature, fiche, récap, refus attendus |
| `tests/test_valeurs_contrat.py` | les VALEURS imprimées : grille (forfait et barème), mensualisation, blocs conditionnels, deux entités sans fuite — sur une config fabriquée en temp |
| `tests/test_signature.py` | `signature.py` contre un transport factice |
| `tests/test_clients.py` | plusieurs clients factices, une instance chacun, étanches |
| `tests/test_produit.py` | balisage client refusé si mal écrit, fiche dérivée, config versionnée, `conservation` malformée refusée, refus de démarrer à deux, règle `templates` dont un `quand` ne peut jamais correspondre refusée |
| `tests/test_import.py` | import SharePoint contre un transport Graph factice : fichiers retenus, doublons, reprise, base, dossier lié |
| `tests/test_excel.py` | lecture `.xlsx`, conversion vers les champs, rapprochement par nom, écran « Compléter » |
| `tests/test_purge.py` | parcours complet → purge → nom/NIR/adresse absents partout, rejouable, jeton de lot révoqué |
| `tests/test_mails.py` | mode console, override `MAILS_MODE`, STARTTLS+login imposés dès qu'un identifiant SMTP est présent |
| `tests/test_configurer.py` | bilan sous cp1252 (sous-processus, pipe), avertissements non bloquants, assistant scripté, comptes, envoi de test contre un faux relais SMTP |

## Ce que le squelette ne fait pas encore

- **Direction visuelle** : CSS sobre au fil de l'eau, pas de `DESIGN.md`, pas de
  passe UI.
- **Comptes RH réels** (ticket 08) : pas d'écran admin, pas de reset self-service,
  pas de verrou anti-brute-force.
- **Relance automatique** à J+3 sur la validation.
- **Anti-robot du formulaire public** (Altcha, honeypot, rate-limit).
- **Export zip** d'un dossier en arborescence `DOSSIERS SALARIES/<Groupe>/<Établissement>/<Poste>/NOM Prénom/`
  depuis l'app : pas encore fait. En attendant, la RH retrouve un dossier par
  l'écran « Salariés » et sa fiche.
- **Guide d'hébergement MongoDB Atlas** détaillé : pas encore écrit.
- **Échéance légale de remise d'un CDD** (2 jours ouvrables) : le type de contrat
  est de la pure config (une règle `templates` peut porter sur n'importe quel
  champ du formulaire), mais aucun délai n'est suivi — seule la date de début
  passe en rouge.
- **Pas d'écran d'administration** de `config/` : c'est l'éditeur qui édite les
  `.json` du client, ce qui suppose un accès au serveur pour redémarrer après
  coup. (Les sociétés, établissements et comptes, eux, sont en base.)
- **`config/` est encore versionné** (fixture sans secret), alors
  que ce dossier est une instance et pas du produit. Le sortir du dépôt suppose
  d'abord de rendre `test_parcours`, `test_ecrans` et `test_signature`
  indépendants de `config/` — ils s'en servent comme jeu de démo. À traiter par
  une instance de démo versionnée sous `tests/`, sur le patron de
  `test_valeurs_contrat`, qui fabrique sa config de bout en bout dans un dossier
  temporaire et ne dépend donc d'aucune instance. **Un clone frais lance
  aujourd'hui toute la suite.**
