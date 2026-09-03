# Contrat_Gen — produit RH auto-hébergé

Parcours complet **formulaire → validation → dossier salarié → contrat →
signature → DPAE → remise au cabinet comptable**, sans base de données et sans
Office365. Implémente les décisions de `.scratch/produit-rh/` et le schéma
Excalidraw (deux couloirs : Dark Kitchen / Restaurant).

**Un client = une copie de ce dépôt**, avec sa config dans `config/`. Rien n'est
mutualisé entre clients ; cette copie-ci porte les valeurs Wingstop.

```bash
pip install -r requirements.txt
python tests/test_parcours.py     # les deux couloirs, de bout en bout
python tests/test_signature.py    # e-sign Yousign contre un transport factice
python tests/test_clients.py      # 2 clients factices, chacun dans son instance
python tests/test_produit.py      # garde-fou de balisage, fiche, config versionnée
python app.py                # http://localhost:5000  (rh / wingstop-rh)
```

## Installer chez un nouveau client

Le **code est le produit**, l'**instance** est un dossier à part qui ne contient
que ce qui appartient au client. `config.exemple/` est le squelette versionné :
une instance ne se copie jamais depuis celle d'un autre.

```bash
python installer.py "ACME Restauration" /srv/acme
CONFIG_DIR=/srv/acme/config DONNEES=/srv/acme/data python app.py
```

`installer.py` **copie** `config.exemple/` vers `/srv/acme/config/` et génère le
secret HMAC et le mot de passe RH. Aucune valeur d'un autre client ne peut
survivre par oubli — c'est structurel, pas une liste de champs à nettoyer. Une
réinstallation par-dessus un `config/` existant est **refusée**.

Il faut ensuite remplir `config/` puis démarrer. **Le démarrage refuse de servir**
tant que l'installation est incomplète : secret encore par défaut, compte RH au
mot de passe par défaut, ou aucun établissement déclaré (`config.verifier()`).
Un secret par défaut rend les liens du lot comptable forgeables, un mot de passe
par défaut ouvre des pièces d'identité — c'est une frontière de confiance.

`config_version` dans `instance.json` fixe le format de la config. Le code
avançant séparément des instances déjà installées, une config d'une autre
version majeure refuse de démarrer en disant quoi faire — l'app ne réécrit
**jamais** la config du client dans son dos.

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
      "mentions": {"AdresseEtablissement": "..."}}]}
]
```

Le **multi-société** *à l'intérieur* d'un client reste possible :
`config/societes.json` de Wingstop contient « Wingstop France » et « Wingstop Sud »,
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
- **`"signature": {"mode": "yousign", "cle_api": "...", "url": "..."}`** : boutons
  « Envoyer à la signature » / « Vérifier la signature » (polling, pas de webhook).
  Livré désactivé — non validable sans clé sandbox du client.

## La démo en 5 minutes

1. `http://localhost:5000` — le formulaire, **rendu depuis `config/formulaire.json`**.
   Remplir, joindre 3 fichiers PDF/JPG.
2. `http://localhost:5000/login` — `rh` / `wingstop-rh`. Le suivi liste soumissions
   et dossiers, triés par date de début, retards en rouge.
3. Ouvrir la demande → **Rejeter** avec un motif → mail de KO avec **lien signé de
   correction** dans la console : l'ouvrir, corriger. L'ancien lien est mort.
4. **Accepter** → dossier salarié (même ULID, changement de zone) ; la **fiche
   salarié** est générée dans `contrat/`. Selon l'établissement : **Générer** ou
   **Déposer** le contrat.
5. **Déposer le contrat signé** → `ContratSigne`.
6. **DPAE** : rappel puis dépôt de l'accusé (refusé sans pièce, refusé avant
   signature). **Remettre au comptable** → mail au cabinet **de la société
   concernée** avec un lien signé ; la page sans login offre « Tout télécharger
   (.zip) » (pièces + contrat + contrat signé + fiche + accusé). **Renvoyer** →
   l'ancien lien renvoie 410.

Les mails ne partent pas : `"mode": "console"` les écrit dans `data/mails/*.eml`.
Passer à `"smtp"` pour de vrais envois (TLS vérifié imposé).

## Récap hebdomadaire des nouveaux salariés

```bash
python recap.py            # 7 derniers jours
python recap.py --jours 14
```

Fenêtre dérivée du journal (entrée `vers == ATraiter`). Destinataires
`config/instance.json → mails.recap` (repli sur `mails.rh`). Aucun scheduler dans
l'app — à mettre en cron / Tâche planifiée côté client, par ex. :

```cron
0 8 * * 1  cd /srv/AcmeRH && /srv/AcmeRH/venv/bin/python recap.py
```

## Ce qui varie par client — en donnée, jamais en code

Tout vit dans `config/` — aucun `.py` n'y entre jamais :

| Fichier | Ce qu'il porte |
|---|---|
| `instance.json` | `config_version`, nom, secret HMAC, signature, fiche salarié, SMTP, destinataires, motifs de KO, comptes, poste → template |
| `formulaire.json` | les champs du formulaire, leur type, le placeholder `.docx` de chacun |
| `societes.json` | sociétés (SIREN, mentions, cabinet) et établissements (SIRET, couloir `contrat`) |
| `contrats/*.docx` | modèles de contrat + `fiche_salarie.docx`, à placeholders `{{Nom}}` |
| `mails/*.txt` | objet + corps de chaque mail |

Au démarrage, `config.verifier()` extrait les `{{placeholders}}` des `.docx`
actifs et refuse ceux qu'aucune source n'alimente — le garde-fou qui rend
l'adaptation à un nouveau client vérifiable.

## Le disque est la base

```
data/
  soumissions/<ULID>/   soumission.json  + pieces/            # avant validation
  documents/<ULID>/     dossier.json     + pieces/ + contrat/ + _versions/
  mails/                                                       # mode console
```

`DONNEES` est surchargeable par variable d'environnement (c'est ce dont les
tests se servent). `dossier.json` porte un **journal append-only** qui fait foi
sur l'état ; un fichier présent est une preuve corroborante, jamais décisive.
Écritures temp-puis-rename avec verrou par id. 9 états :
`Soumise → Rejetee / ATraiter → ContratPret → ContratSigne → RappelDpae →
DpaeFaite → RemisComptable`, plus `Abandonnee`. Les transitions permises sont
dans `store.TRANSITIONS` — un `POST` hors séquence est refusé côté serveur, pas
seulement caché dans le template.

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
| `recap.py` | récap hebdomadaire des nouveaux salariés (CLI, à mettre en cron) |
| `tests/test_parcours.py` | les deux couloirs, signature, fiche, récap, refus attendus |
| `tests/test_signature.py` | `signature.py` contre un transport factice |
| `tests/test_clients.py` | plusieurs clients factices, une instance chacun, étanches |
| `tests/test_produit.py` | balisage client refusé si mal écrit, fiche dérivée, config versionnée, rechargement à chaud |

## Ce que le squelette ne fait pas encore

- **Direction visuelle** : CSS sobre au fil de l'eau, pas de `DESIGN.md`, pas de
  passe UI.
- **Purge RGPD à deux étages** (ticket 01) : buckets en place, commande non écrite.
- **Comptes RH réels** (ticket 08) : pas d'écran admin, pas de reset self-service,
  pas de verrou anti-brute-force.
- **Relance automatique** à J+3 sur la validation.
- **Anti-robot du formulaire public** (Altcha, honeypot, rate-limit).
- **Scan de démarrage** signalant les écarts disque / `dossier.json`.
- **Échéance légale de remise d'un CDD** (2 jours ouvrables) : le type de contrat
  est de la pure config (une règle `templates` peut porter sur n'importe quel
  champ du formulaire), mais aucun délai n'est suivi — seule la date de début
  passe en rouge.
- **Pas d'écran d'administration** : c'est l'éditeur qui édite les `.json` du
  client. Le rechargement à chaud rend ça tenable sans accès au serveur.
- **`config/` est encore versionné**, secret et hash du compte RH compris, alors
  que ce dossier est une instance et pas du produit. Le sortir du dépôt suppose
  d'abord de rendre `test_parcours`, `test_ecrans` et `test_signature`
  indépendants de `config/` — ils s'en servent comme jeu de démo. Deux tests
  (`test_wingstop`, `test_parcours_wingstop`) sont déjà dans ce cas avec
  `config_wingstop/`, ignoré de longue date : **un clone frais ne peut pas
  lancer toute la suite**. À traiter par une instance de démo versionnée sous
  `tests/`.
