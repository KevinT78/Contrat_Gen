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
python tests/test_clients.py      # 2 clients factices, chacun dans sa copie du repo
python app.py                # http://localhost:5000  (rh / wingstop-rh)
```

## Installer chez un nouveau client

```bash
cp -r Contrat_Gen/ AcmeRH/ && cd AcmeRH/
python installer.py "ACME Restauration"   # secret HMAC + mot de passe RH générés
```

`installer.py` agit **sur place** : il génère les secrets, vide `config/societes.json`,
les `templates` et `fiche_salarie` de `config/instance.json`, et supprime les
`.docx` de `config/contrats/`. Ne restent que le squelette commun (formulaire,
motifs de KO, textes des mails) et le compte RH.

Il faut ensuite remplir `config/` puis démarrer. **Le démarrage refuse de servir**
tant que l'installation est incomplète : secret encore par défaut, compte RH au
mot de passe par défaut, ou aucun établissement déclaré (`config.verifier()`).
Un secret par défaut rend les liens du lot comptable forgeables, un mot de passe
par défaut ouvre des pièces d'identité — c'est une frontière de confiance.

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
| `instance.json` | nom, secret HMAC, signature, fiche salarié, SMTP, destinataires, motifs de KO, comptes, poste → template |
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
| `installer.py` | remet `config/` à blanc pour un nouveau client |
| `recap.py` | récap hebdomadaire des nouveaux salariés (CLI, à mettre en cron) |
| `tests/test_parcours.py` | les deux couloirs, signature, fiche, récap, refus attendus |
| `tests/test_signature.py` | `signature.py` contre un transport factice |
| `tests/test_clients.py` | plusieurs clients factices, une copie du repo chacun, copies étanches |

## Ce que le squelette ne fait pas encore

- **Direction visuelle** : CSS sobre au fil de l'eau, pas de `DESIGN.md`, pas de
  passe UI.
- **Purge RGPD à deux étages** (ticket 01) : buckets en place, commande non écrite.
- **Comptes RH réels** (ticket 08) : pas d'écran admin, pas de reset self-service,
  pas de verrou anti-brute-force.
- **Relance automatique** à J+3 sur la validation.
- **Anti-robot du formulaire public** (Altcha, honeypot, rate-limit).
- **Scan de démarrage** signalant les écarts disque / `dossier.json`.
- Config lue une fois au démarrage : éditer un `.json` demande un redémarrage.
