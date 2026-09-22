# Contrat_Gen — produit RH auto-hébergé

Parcours complet **formulaire → validation → dossier salarié → contrat →
signature → DPAE → remise au cabinet comptable**, sans base de données et sans
Office365. Implémente les décisions de `.scratch/produit-rh/` et le schéma
Excalidraw (deux couloirs : Dark Kitchen / Restaurant).

**Un client = une copie de ce dépôt**, avec sa config dans `config/`. Rien n'est
mutualisé entre clients.

Le `config/` versionné ici est une **fixture de démonstration**, pas un
déploiement : son secret et son mot de passe sont publics et le sont
volontairement. Un déploiement réel se pose hors du dépôt avec `installer.py`
(voir plus bas), qui génère son propre secret et son propre mot de passe RH.

```bash
pip install -r requirements.txt
python tests/test_parcours.py     # les deux couloirs, de bout en bout
python tests/test_signature.py    # e-sign Yousign contre un transport factice
python tests/test_clients.py      # 2 clients factices, chacun dans son instance
python tests/test_produit.py      # garde-fou de balisage, fiche, config versionnée
DEBUG=1 python app.py        # http://localhost:5000  (rh / fixture)
```

## Installer chez un nouveau client

Le **code est le produit**, l'**instance** est un dossier à part qui ne contient
que ce qui appartient au client. `config.exemple/` est le squelette versionné :
une instance ne se copie jamais depuis celle d'un autre.

```bash
python installer.py "ACME Restauration" /srv/acme
CONFIG_DIR=/srv/acme/config python app.py
```

`installer.py` **copie** `config.exemple/` vers `/srv/acme/config/` et génère le
secret HMAC et le mot de passe RH. Aucune valeur d'un autre client ne peut
survivre par oubli — c'est structurel, pas une liste de champs à nettoyer. Une
réinstallation par-dessus un `config/` existant est **refusée**.

### Où vivent les données : local ou dossier synchronisé

L'installateur prend un **3e argument optionnel** : le chemin d'un dossier
répliqué par le client de synchronisation d'un drive d'entreprise (OneDrive,
SharePoint, Google Drive) **déjà installé sur ce serveur**.

```bash
python installer.py "ACME" /srv/acme                                   # data/ dans l'instance
python installer.py "ACME" /srv/acme "C:/Users/rh/OneDrive - ACME/Embauches"   # sur le drive
```

Le choix est écrit dans `config/instance.json` (`"stockage": {"mode": "local"}`
ou `{"mode": "dossier", "chemin": "…"}`) et **vérifié au démarrage** : mode
connu, dossier existant, et écriture réellement testée — un dossier de sync pas
encore répliqué ou monté en lecture seule est refusé tout de suite, pas découvert
à la première soumission d'un vrai salarié. Un bloc absent vaut `local` : les
instances installées avant ce bloc démarrent sans rien changer. Pour le poser ou
le corriger après coup : `configurer.py --assister`. `DONNEES=` sur la ligne de
lancement reste **prioritaire** sur le bloc. Ce n'est pas rechargeable à chaud :
changer `stockage` puis *Recharger la configuration* affiche un avertissement —
les écritures continuent d'aller à l'ancien endroit jusqu'au redémarrage (et il
faut déplacer les fichiers). L'installateur **refuse** un dossier drive non vide,
comme il refuse d'écraser un `config/` existant.

En mode `dossier`, l'app écrit sur un système de fichiers local comme avant : elle
ne fait **aucun appel réseau** et ne connaît aucun fournisseur — c'est le client
de sync qui réplique. Quatre choses à savoir (détail et raisonnement dans
[docs/ADR-stockage.md](docs/ADR-stockage.md)) :

- **Une seule machine écrit.** `store._verrou` n'exclut qu'à l'intérieur d'un
  process : deux serveurs pointés sur le même dossier drive perdraient des
  entrées de journal. Ce n'est pas un stockage partagé, c'est un disque distant.
- **Verrouillage pendant l'upload.** Le client de sync peut tenir un fichier le
  temps de le téléverser ; sous Windows, `os.replace` / `shutil.move` peuvent
  alors lever `PermissionError`. Non observé en conditions réelles à ce jour —
  aucune parade n'est codée tant que ce n'est pas mesuré.
- **Fichiers à la demande** (OneDrive) : un fichier non téléchargé se lit quand
  même, mais lentement — à surveiller sur `/suivi`, qui rescanne tous les
  `dossier.json`.
- **La sauvegarde change de forme** : le drive porte l'historique de `data/`,
  `tar` reste la sauvegarde de `config/` (qui, lui, ne quitte jamais le serveur).
- **La purge locale ne descend pas dans le drive** : effacer les pièces d'un
  dossier terminé (bloc `conservation`, ci-dessous) ne vide **ni la corbeille ni
  l'historique de versions** du service de synchronisation — la version complète
  de `dossier.json`, NIR et adresse compris, y reste récupérable. Après une
  purge en mode `dossier`, vider la corbeille et l'historique de versions à la
  main côté service. `python purger.py` le rappelle.

Le mode « API cloud directe » (Graph, Google Drive API, OAuth) n'est **pas**
codé : il exigerait de rejouer le seam d'I/O de `store.py` et surtout de décider
où vit le journal `dossier.json`. Voir l'ADR.

Il faut ensuite remplir `config/` puis démarrer. **Le démarrage refuse de servir**
tant que l'installation est incomplète : secret encore par défaut, compte RH au
mot de passe par défaut, aucun établissement déclaré, `mails.rh` /
`mails.expediteur` vides, ou `url` (adresse publique, base des liens du mail
hebdo) vide (`config.verifier()`). Sans destinataire RH, chaque
demande partirait en `mail_echoue` dans le journal sans que personne ne le voie.

`configurer.py` rend ce garde-fou lisible **avant** de lancer le serveur, et
enchaîne `placeholders` puis `doctor` dès que la config passe :

```bash
CONFIG_DIR=/srv/acme/config python configurer.py             # bilan : chaque manque, et le fichier à ouvrir
CONFIG_DIR=/srv/acme/config python configurer.py --assister  # pose les questions (mails, comptes, société), boucle jusqu'à OK
CONFIG_DIR=/srv/acme/config python configurer.py compte marc # ajoute ou remplace un compte
CONFIG_DIR=/srv/acme/config python configurer.py mail vous@acme.fr  # envoi de test en SMTP, même en mode console
```

L'assistant n'écrit que ce qui se dicte : adresses, comptes, société et
établissements, secret. Rôles, balisage des modèles et grille restent à éditer
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
écrit dans le bloc `utilisateurs` de `instance.json`).

Tous les comptes ont les mêmes droits ; le journal de chaque dossier porte qui a
fait quoi.
Un secret par défaut rend les liens du lot comptable forgeables, un mot de passe
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
PROXIES=1 CONFIG_DIR=/srv/acme/config DONNEES=/srv/acme/data python app.py
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
construction, et c'est voulu : `store._verrou` n'exclut qu'à l'intérieur d'un
process, donc deux process écriraient le même `dossier.json` en concurrence et
perdraient des entrées de journal. Ne pas servir cette app depuis un serveur
multi-process sans avoir d'abord posé un verrou fichier (voir `store._verrou`).

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
| `$DONNEES` (`data/`, ou le dossier drive) | le client | **jamais touché** |

Donc : remplacer le code, relancer. Au démarrage, `config.verifier()` refuse de
servir si la nouvelle version réclame quelque chose que la config n'a pas — un
placeholder sans source, un rôle manquant, un établissement absent — et dit quoi
corriger, plutôt que de démarrer à moitié. Si le **format** de config a changé de
version majeure, `config_version` fait refuser le démarrage avec la marche à
suivre : l'app ne réécrit jamais la config du client dans son dos.

Une config peut aussi être rechargée sans redémarrer, depuis l'écran de suivi
(bouton *Recharger la configuration*) : une config invalide ne prend pas et
l'ancienne reste active.

**Sauvegarder.** (En mode `dossier`, c'est le drive qui porte l'historique de
`$DONNEES` ; ce qui suit ne concerne alors plus que `config/`.)
`$DONNEES` **est** la base : soumissions, dossiers, pièces
d'identité, RIB, contrats générés et signés. `$CONFIG_DIR` contient le secret et
le compte RH. Les deux, rien d'autre :

```bash
tar czf sauvegarde-$(date +%F).tar.gz /srv/acme/config /srv/acme/data
```

Restaurer = détarrer et relancer. Pas de dump, pas d'ordre de restauration : il
n'y a pas de base de données, et c'est précisément ce que ça achète.

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

Nouvel établissement, salaire revalorisé, clause modifiée : éditer les `.json`,
puis **« Recharger la configuration »** depuis l'écran de suivi — pas de
redémarrage, donc pas besoin d'un accès au serveur du client. Une config
invalide ne prend pas : l'ancienne reste active et l'écran dit ce qui cloche.

`config/societes.json` (forme attendue) :

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
rejet (lien de correction) y part, quel que soit l'email tapé dans le formulaire ;
sans elle, il retombe sur l'email saisi.

### Qui reçoit quoi

Expéditeur unique : `mails.expediteur`.

| Quand | Modèle | Destinataire |
|---|---|---|
| Soumission ou correction du formulaire | `nouvelle_soumission` | `mails.rh` |
| Rejet par la RH | `rejet` | `manager_email` de l'établissement, repli email saisi |
| Validation par la RH | `rappel_dpae` | `mails.dpae`, repli `mails.rh` |
| Cron hebdomadaire (`recap.py`) | `recap_hebdo` | le cabinet de chaque société, `mails.rh` en copie |

Le **multi-société** *à l'intérieur* d'un client reste possible :
la fixture `config/societes.json` contient « ACME Restauration » et « ACME Sud »,
deux SIREN du même client, chacun avec ses établissements et son cabinet. Le
multi-tenant (une copie servant plusieurs entreprises) est hors périmètre —
pièces d'identité et RIB, une fuite inter-client serait un incident RGPD.

## Les deux couloirs

Chaque établissement porte `"contrat"` dans `config/societes.json` :

| Valeur | Couloir | À l'état `ATraiter` |
|---|---|---|
| `genere` (défaut) | Dark Kitchen | **Générer le contrat** — `.docx` rempli depuis le template du poste |
| `depose` | Restaurant | **Déposer le contrat** — fait à la main sur myrhis, PDF ou `.docx` |

Les deux voies mènent à `ContratPret`, puis à `ContratSigne`.

## Signature

- **`"signature": {"mode": "manuel"}`** (défaut) : la RH télécharge le contrat, le
  fait signer hors app, dépose le PDF signé → `ContratSigne`.

## La démo en 5 minutes

`python demo.py` remet `data_demo/` à zéro, amorce quatre dossiers à des étapes
différentes (demande reçue, demande validée en contrat déposé, contrat prêt,
remis au cabinet) sur la config de démo fictive versionnée (`config.demo/`,
« Basilic Café ») et sert sur `:5000` — identifiants `rh` / `demo-rh`.
`python demo.py --vide` démarre sur un suivi vide pour tout jouer à la main. Mail
hebdo de la démo : `CONFIG_DIR=config.demo DONNEES=data_demo python recap.py`.
Le déroulé ci-dessous vaut aussi pour la fixture `config/` (`rh` / `fixture`).

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
5. **Déposer le contrat signé** → `ContratSigne` (dépôt manuel du PDF signé).
6. **DPAE** : dépôt de l'accusé (refusé sans pièce, refusé avant
   signature). **Remettre au comptable** → le dossier est **dupliqué** dans
   `data/COMPTA/<GROUPE>/<ETABLISSEMENT>/<POSTE>/<NOM PRENOM - id>/`
   (`FICHE PERSONNELLE/` + `CONTRAT/`, segments en capitales sans accents,
   fichiers renommés lisiblement), miroitable sur un Drive. Aucun mail à ce moment : le cabinet
   **de la société concernée** reçoit le lien signé dans le mail hebdomadaire
   (`recap.py`) ; la page sans login offre « Tout télécharger (.zip) ».
   **Refaire la copie et le lien** → l'ancien lien renvoie 410, le dossier repart
   dans le prochain mail hebdo.

Les mails ne partent pas : `"mode": "console"` les écrit dans `data/mails/*.eml`.
Passer à `"smtp"` pour de vrais envois ; la variable d'env `MAILS_MODE` surclasse
le fichier. Dès qu'un `mails.utilisateur` est renseigné, STARTTLS vérifié + login
sont imposés ; sans identifiant, l'envoi part en clair (catcher local type MailHog).

**Tester les mails localement** : `lancer_test_emails.bat` lance MailHog (SMTP
`localhost:1025`, UI `http://localhost:8025`) et l'app avec `MAILS_MODE=smtp` —
`config/instance.json` n'est pas modifié. MailHog s'installe via
`scoop install mailhog` ou depuis les *releases* GitHub `mailhog/MailHog`.

## Mail hebdomadaire au cabinet comptable

```bash
python recap.py            # dossiers remis ces 7 derniers jours
python recap.py --jours 14
```

**Un mail par cabinet** (`comptable_email` de la société, repli
`mails.comptable_defaut`), la RH en copie (`mails.recap`, repli `mails.rh`) : les
dossiers remis au comptable sur la fenêtre (journal `vers == RemisComptable`, ou
un renvoi), chacun avec son **lien de lot signé** (30 jours). Un cabinet ne voit
jamais les dossiers d'une autre société ; aucun mail à un cabinet sans dossier.
Les liens sont fabriqués hors requête : `instance.json → url` (adresse publique
de l'app) est obligatoire, le démarrage le refuse vide. Aucun scheduler dans
l'app — à mettre en cron / Tâche planifiée côté client, par ex. :

```cron
0 8 * * 1  cd /srv/AcmeRH && /srv/AcmeRH/venv/bin/python recap.py
```

## Ce qui varie par client — en donnée, jamais en code

Tout vit dans `config/` — aucun `.py` n'y entre jamais :

| Fichier | Ce qu'il porte |
|---|---|
| `instance.json` | `config_version`, nom, secret HMAC, signature, fiche salarié, SMTP, destinataires, motifs de KO, comptes, poste → template |
| `formulaire.json` | les champs du formulaire, leur type, le placeholder `.docx` de chacun, et la table `roles` |
| `societes.json` | sociétés (SIREN, mentions, cabinet) et établissements (SIRET, couloir `contrat`) |
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

## Le disque est la base

```
data/
  soumissions/<ULID>/   soumission.json  + pieces/            # avant validation
  documents/<ULID>/     dossier.json     + pieces/ + contrat/ + _versions/
  mails/                                                       # mode console
```

Où vit `data/` se décide à l'installation (`stockage` dans `instance.json`,
voir plus haut) ; `DONNEES` reste surchargeable par variable d'environnement et
l'emporte sur le bloc (c'est ce dont les tests se servent). `dossier.json` porte un **journal append-only** qui fait foi
sur l'état ; un fichier présent est une preuve corroborante, jamais décisive.
Écritures temp-puis-rename avec verrou par id. 9 états :
`Soumise → Rejetee / ATraiter → ContratPret → ContratSigne →
DpaeFaite → RemisComptable → Parti`, plus `Abandonnee`. Le rappel DPAE n'est pas un
état : c'est un effet de la validation, tracé dans le journal seulement s'il
échoue (`mail_echoue`). Les transitions permises sont
dans `store.TRANSITIONS` — un `POST` hors séquence est refusé côté serveur, pas
seulement caché dans le template. `Parti` est terminal (aucune transition
sortante) : le départ d'un salarié (bouton « Archiver dans LEAVERS », onglet
« Anciens salariés » de `/salaries`) déplace réellement son dossier de
`DOSSIERS SALARIES/` vers `LEAVERS/`, même sous-chemin établissement / poste /
nom — c'est l'arborescence que la RH ouvre à la main, un marquage logique seul
ne lui donnerait pas le dossier LEAVERS qu'elle demande.

**Refus de démarrer à deux.** En production (waitress), `demarrer()` pose
`<data>/.serveur-actif.json` (`{hote, pid, le}`, rafraîchi toutes les 60 s par
un thread démon) et refuse le démarrage si un autre serveur le tient —
`store._verrou` est intra-process, deux serveurs sur le même stockage perdent
des entrées de journal en silence. Un verrou dont le process est mort **sur la
même machine** est repris aussitôt (sonde de vivacité) ; venu d'une autre
machine, il expire au bout de 5 minutes. Pour forcer, supprimer le fichier. Le
mode `DEBUG` (dev local) ne pose pas de verrou.

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
pièces (CNI, RIB, contrats, copies comptables), réduit `dossier.json` à
établissement / poste / date de début, nettoie le journal (commentaires libres,
IP, procédures Yousign jetés), garde une entrée `purge` et renomme le répertoire
`purge-<id>`. **Aucun octet supprimé sans trace : le journal reste.** Bloc
absent = aucune purge (rétro-compatible). La purge s'exécute dans une requête,
jamais dans un cron (invariant mono-process). `python purger.py` est le pendant
**lecture seule** : liste les éligibles (code de sortie non nul s'il en reste),
les `dossier.json` orphelins hors profondeur de `store._scan()`, et
l'avertissement drive.

## Fichiers

| | |
|---|---|
| `app.py` | routes et parcours ; aucune action au `GET` |
| `store.py` | disque, journal, états + `TRANSITIONS`, liens signés HMAC |
| `contrat.py` | remplissage des `.docx` (`paragraphes`/`remplacer` repris de wingstop_) |
| `config.py` | lecture de `config/` + garde-fou de démarrage |
| `mails.py` | rendu des templates + SMTP ou console |
| `signature.py` | e-sign Yousign, optionnel, désactivé par défaut |
| `installer.py` | crée l'instance d'un nouveau client depuis `config.exemple/` |
| `placeholders.py` | fiche des `{{Jetons}}` à remettre au client, dérivée de sa config |
| `recap.py` | mail hebdomadaire au cabinet : dossiers remis + liens de lot (CLI, à mettre en cron) |
| `purger.py` | pendant lecture seule de la purge : éligibles, orphelins, avertissement drive (CLI) |
| `doctor.py` | couverture de la config, vérifiée en produisant les contrats |
| `configurer.py` | bilan de config lisible (avec avertissements de mise en production), assistant interactif, ajout de comptes, envoi de test SMTP |
| `tests/test_parcours.py` | les deux couloirs, signature, fiche, récap, refus attendus |
| `tests/test_valeurs_contrat.py` | les VALEURS imprimées : grille (forfait et barème), mensualisation, blocs conditionnels, deux entités sans fuite — sur une config fabriquée en temp |
| `tests/test_signature.py` | `signature.py` contre un transport factice |
| `tests/test_clients.py` | plusieurs clients factices, une instance chacun, étanches |
| `tests/test_produit.py` | balisage client refusé si mal écrit, fiche dérivée, config versionnée, rechargement à chaud, `conservation` malformée refusée, refus de démarrer à deux, règle `templates` dont un `quand` ne peut jamais correspondre refusée |
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
- **Purge côté service de synchronisation** : la purge locale ne vide ni la
  corbeille ni l'historique de versions du drive (à faire à la main).
- **Verrou fichier multi-process réel** (`portalocker`) : `.serveur-actif.json`
  prévient l'erreur de déploiement, il n'arbitre pas une course.
- **Échéance légale de remise d'un CDD** (2 jours ouvrables) : le type de contrat
  est de la pure config (une règle `templates` peut porter sur n'importe quel
  champ du formulaire), mais aucun délai n'est suivi — seule la date de début
  passe en rouge.
- **Pas d'écran d'administration** : c'est l'éditeur qui édite les `.json` du
  client. Le rechargement à chaud rend ça tenable sans accès au serveur.
- **`config/` est encore versionné**, secret et hash du compte RH compris, alors
  que ce dossier est une instance et pas du produit. Le sortir du dépôt suppose
  d'abord de rendre `test_parcours`, `test_ecrans` et `test_signature`
  indépendants de `config/` — ils s'en servent comme jeu de démo. À traiter par
  une instance de démo versionnée sous `tests/`, sur le patron de
  `test_valeurs_contrat`, qui fabrique sa config de bout en bout dans un dossier
  temporaire et ne dépend donc d'aucune instance. **Un clone frais lance
  aujourd'hui toute la suite.**
