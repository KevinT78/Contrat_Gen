# Guide de déploiement — Contrat_Gen

Ce guide couvre trois choses :

1. **Mettre la démo en ligne** sur un VPS (partie 1) ;
2. **Déployer l'instance d'un client** sur ce même VPS (partie 2) ;
3. **Les points à surveiller** : e-mails, authentification, proxy, stockage,
   RGPD (partie 3).

Il a été rédigé à partir du code (`app.py`, `config.py`, `installer.py`, `configurer.py`,
`mails.py`, `recap.py`, `purger.py`, `store.py`, `demo.py`). Les
exemples de commandes visent **Debian 12 ou Ubuntu 24.04** avec systemd et Caddy.

---

## 0. Ce qu'il faut comprendre avant de commencer

### Le code et les instances sont deux choses séparées

| | Où | Contenu | Qui écrit |
|---|---|---|---|
| **Code** | `/opt/contrat-gen` (clone git) | `app.py`, `templates/`, `modeles/`, `config.exemple/`, `config.demo/` | `git pull` uniquement |
| **Instance** | `/srv/contratgen/<client>/` | `config/` (secrets, comptes, sociétés, modèles) + `data/` (dossiers, pièces) | l'app, `installer.py`, `configurer.py` |

- Le même code peut servir plusieurs instances. Chacune a son process, son port et son
  sous-domaine.
- Une instance est désignée par la variable **`CONFIG_DIR`**. Sans elle, l'app lit
  `config/` à la racine du dépôt. C'est une **fixture de test**, jamais une vraie instance.
- Quand `CONFIG_DIR` est posé, les données vont par défaut dans `CONFIG_DIR/../data`
  (`config._donnees()`).

### Un seul process par instance, c'est imposé

Plusieurs mécanismes vivent **en mémoire du process** : les verrous d'écriture des
dossiers (`store._verrou`), les compteurs anti-flood (formulaire et login) et le cache de
config. `python app.py` démarre donc **waitress** : un seul process, 4 threads.

- **Ne jamais** lancer `gunicorn app:app` ni `waitress-serve --processes=N`. Rien ne
  vous préviendrait, et des entrées de journal seraient perdues.
- Un fichier `data/.serveur-actif.json` empêche un second serveur de démarrer sur le
  même stockage. Il est rafraîchi toutes les 60 s.

### Le démarrage refuse une config incomplète

`config.verifier()` s'exécute avant de servir. Si un contrôle échoue, le process affiche
`!! sujet : raison` puis s'arrête avec un code d'erreur. Les contrôles :

- format de config (`config_version`) ;
- comptes ;
- stockage (écriture testée pour de vrai) ;
- conservation ;
- secret encore à `REMPLACER-A-L-INSTALLATION` ;
- mot de passe `demo` ;
- aucun établissement ;
- `mails.rh` ou `mails.expediteur` vides ;
- `url` vide ;
- règles `derives` ;
- rôles du formulaire ;
- jetons `{{...}}` des modèles qu'aucune source n'alimente ;
- règles `templates` (forme liste) dont un `quand` ne peut jamais correspondre
  (champ inconnu, option mal orthographiée, établissement écrit sans sa
  société) ;
- postes sans modèle ;
- grille.

Sous systemd, ces messages sont dans `journalctl -u <service>`.

Pour obtenir ce même bilan **sans démarrer le serveur** : `python configurer.py`
(détails en §2.3).

### Variables d'environnement lues par le code

| Variable | Défaut | Rôle |
|---|---|---|
| `CONFIG_DIR` | `<dépôt>/config` | Dossier `config/` de l'instance. **Obligatoire en production.** |
| `DONNEES` | voir ci-dessus | Force l'emplacement de `data/`. Prioritaire sur le bloc `stockage`. |
| `PORT` | `5000` | Port d'écoute de waitress. |
| `HOST` | `127.0.0.1` | Adresse d'écoute. Laisser en local, derrière le reverse proxy. |
| `PROXIES` | `0` | Nombre **réel** de reverse proxies devant l'app. **`1` derrière Caddy.** Voir §3.3. |
| `DEBUG` | vide | Serveur Werkzeug + debugger, cookie non `Secure`. **Jamais en production.** |
| `MAILS_MODE` | vide | Remplace `mails.mode` d'`instance.json` (`console` / `smtp`). Utile pour les tests. |

---

## Partie 1 — Déployer la démo sur un VPS

La démo tourne sur `demo.py`, qui s'appuie sur `config.demo/`. Les données sont effacées
et 4 dossiers d'exemple recréés **à chaque démarrage**. Les mails ne partent pas : ils
sont écrits dans des fichiers `.eml` et dans le journal (voir §1.8).

### 1.1 Prérequis

- Un VPS (1 vCPU / 1 Go de RAM suffit), sous Debian 12 ou Ubuntu 24.04, de préférence
  hébergé dans l'UE.
- Un nom de domaine, avec un enregistrement DNS `A` (et `AAAA` si IPv6) de
  `demo.votre-domaine.fr` vers l'IP du VPS, **avant** de configurer Caddy (sinon le
  certificat Let's Encrypt échoue).
- Python ≥ 3.10. Debian 12 fournit la 3.11, Ubuntu 24.04 la 3.12, et le projet est
  développé en 3.12.
- **`config.demo/` doit être poussé.** Au moment où ce guide est écrit, il est commité
  (`7fc7402`) mais pas poussé : un clone de GitHub ne le contient pas (vérifié sur un clone
  neuf), et la copie du §1.4 échouerait.

### 1.2 Préparer le serveur

```bash
sudo apt update && sudo apt install -y python3 python3-venv git ufw
sudo timedatectl set-timezone Europe/Paris      # l'alerte CDD utilise la date locale

# pare-feu : SSH + HTTP(S) seulement ; l'app n'écoute que sur 127.0.0.1
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# utilisateur système qui fera tourner toutes les instances
sudo useradd --system --home /srv/contratgen --shell /usr/sbin/nologin contratgen
sudo mkdir -p /srv/contratgen
sudo chown contratgen: /srv/contratgen
sudo chmod 750 /srv/contratgen
```

Côté SSH, n'autoriser que les clés (`PasswordAuthentication no`) et activer
`unattended-upgrades`.

### 1.3 Installer le code

```bash
sudo git clone https://github.com/KevinT78/Contrat_Gen.git /opt/contrat-gen
sudo python3 -m venv /opt/contrat-gen/venv
sudo /opt/contrat-gen/venv/bin/pip install -r /opt/contrat-gen/requirements.txt
```

Si le dépôt est privé, deux options :

- une **deploy key** en lecture seule (`ssh-keygen -t ed25519`, clé publique ajoutée
  dans *Settings → Deploy keys* sur GitHub), puis clone en `git@github.com:...` ;
- un token à portée restreinte.

Le code appartient à root et reste en lecture seule pour `contratgen`, ce qui est voulu :
l'app n'écrit jamais dans son propre dossier.

Dépendances : `flask`, `python-docx`, `htmldocx`, `num2words`, `waitress`. Toutes sont en
pur Python ou disponibles en wheels. Aucun paquet système n'est nécessaire : pas de
LibreOffice, les contrats sortent directement en `.docx`.

### 1.4 Créer l'instance de démo

On travaille sur une **copie** de `config.demo/`, hors du dépôt. Deux raisons :

- le secret de `config.demo/instance.json` est versionné, donc connu de quiconque a accès
  au dépôt et identique sur toutes les copies : il permettrait de forger des cookies de
  session et des liens signés ;
- son `url` pointe sur `http://localhost:5000`.

```bash
sudo -u contratgen mkdir -p /srv/contratgen/demo
sudo -u contratgen cp -r /opt/contrat-gen/config.demo /srv/contratgen/demo/config
python3 -c "import secrets; print(secrets.token_hex(32))"     # nouveau secret
sudo -u contratgen nano /srv/contratgen/demo/config/instance.json
```

Dans `instance.json`, modifier :

- `"secret"` : coller la valeur générée ;
- `"url"` : `"https://demo.votre-domaine.fr"` (sert de base aux liens du mail hebdo) ;
- laisser `"mails": {"mode": "console", ...}` : la démo n'envoie rien.

> ⚠️ **Ne changez pas le mot de passe du compte `rh`.** `demo.py` se connecte en dur avec
> `rh` / `demo-rh` pour préparer les dossiers d'exemple. Avec un autre mot de passe, la
> connexion échoue sans message, l'assertion `RemisComptable` casse et le service redémarre
> en boucle. Ces identifiants étant publics (ils figurent dans `demo.py`), protégez le site
> par une authentification Caddy (§1.6).
> Seule exception : le mode `--vide`, qui ne prépare aucun dossier.
> `configurer.py compte rh` y fonctionne normalement.

Vérifier la config avant de lancer :

```bash
cd /opt/contrat-gen
sudo -u contratgen env CONFIG_DIR=/srv/contratgen/demo/config \
  DONNEES=/srv/contratgen/demo/data \
  venv/bin/python -c "import config; print(config.verifier() or 'OK')"
```

### 1.5 Service systemd

`/etc/systemd/system/contratgen-demo.service` :

```ini
[Unit]
Description=Contrat_Gen — démo
After=network-online.target
Wants=network-online.target

[Service]
User=contratgen
Group=contratgen
WorkingDirectory=/opt/contrat-gen
Environment=CONFIG_DIR=/srv/contratgen/demo/config
# DONNEES est EFFACÉ à chaque démarrage par demo.py : dossier dédié, jamais celui d'un client
Environment=DONNEES=/srv/contratgen/demo/data
Environment=PORT=5000
Environment=PROXIES=1
ExecStart=/opt/contrat-gen/venv/bin/python demo.py
Restart=on-failure
RestartSec=5

# durcissement
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/srv/contratgen/demo

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now contratgen-demo
journalctl -u contratgen-demo -f      # attendre « waitress sur 127.0.0.1:5000 »
```

Les URL `http://localhost:5000` affichées par `demo.py` au démarrage concernent un
lancement local. Ignorez-les.

### 1.6 Reverse proxy HTTPS (Caddy)

HTTPS est **obligatoire** : le cookie de session est marqué `Secure` dès que `DEBUG`
n'est pas posé. En HTTP simple, la connexion RH ne tient pas et renvoie sur `/login`.

Installation (dépôt officiel Caddy) :

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Générer un mot de passe pour le verrou d'accès à la démo :

```bash
caddy hash-password        # saisir le mot de passe à donner aux prospects
```

`/etc/caddy/Caddyfile` :

```caddy
demo.votre-domaine.fr {
    encode gzip
    basic_auth {
        demo <hash produit ci-dessus>
    }
    reverse_proxy 127.0.0.1:5000
}
```

```bash
sudo systemctl reload caddy
```

Caddy obtient le certificat tout seul et transmet l'en-tête `Host` d'origine. Grâce à
`PROXIES=1`, les liens absolus (mails, correction) sont bien en `https://demo.votre-domaine.fr/...`.

### 1.7 Vérifier la mise en ligne

- [ ] `https://demo.votre-domaine.fr/` affiche le formulaire (après le mot de passe Caddy).
- [ ] `/login` avec `rh` / `demo-rh` ouvre le suivi, et **la session tient** en changeant
      de page.
- [ ] Le suivi montre les 4 dossiers : BENALI (à valider), SOARES (contrat à déposer),
      NKEMBA (contrat prêt), DUPONT (dans *Salariés*).
- [ ] Une soumission via le formulaire apparaît dans le suivi, et un mail
      `nouvelle_soumission` s'affiche dans `journalctl -u contratgen-demo`.
- [ ] Le lien de ce mail commence par `https://demo.votre-domaine.fr/`. S'il commence par
      `http://`, les en-têtes du proxy n'arrivent pas à l'app (§3.3).
- [ ] `curl -I http://127.0.0.1:5000/` répond sur le VPS, alors que `http://<IP>:5000`
      depuis l'extérieur est refusé (pare-feu + écoute locale).

### 1.8 Particularités de la démo

- **Remise à zéro à chaque (re)démarrage**, y compris après un plantage ou un reboot.
  Pour repartir d'une démo propre chaque nuit, ajouter dans la crontab de root :
  `0 4 * * * systemctl restart contratgen-demo`.
- **Mails** : visibles en direct avec `journalctl -u contratgen-demo -f`, et en fichiers
  dans `/srv/contratgen/demo/data/mails/*.eml`.
- **Mail hebdo au cabinet** (le dossier DUPONT y figure) :
  ```bash
  cd /opt/contrat-gen
  sudo -u contratgen env CONFIG_DIR=/srv/contratgen/demo/config \
    DONNEES=/srv/contratgen/demo/data venv/bin/python recap.py
  ```
- **Mode `--vide`** (parcours intégralement manuel) : remplacer `demo.py` par
  `demo.py --vide` dans `ExecStart`. Les PDF d'exemple sont créés **sur le serveur**, ce
  qui ne sert à rien pour une présentation à distance : préparez vos propres PDF en local.
- **Ne lancez jamais `demo.py` à la main pendant que le service tourne** : il effacerait
  les données du service en marche.
- **Mise à jour** : après un `git pull` qui modifie `config.demo/`, recopiez-la en
  conservant votre `secret` et votre `url`.

---

## Partie 2 — Déployer l'instance d'un client

Principe : **un client = un dossier `/srv/contratgen/<client>/` + un port + un
sous-domaine + une unité systemd**. Le code dans `/opt/contrat-gen` est partagé.

Pour l'exemple : client « ACME Restauration », identifiant court `acme`, port `5101`,
domaine `embauche.acme.fr`.

### 2.1 Raccourci pratique

Toutes les commandes d'administration doivent tourner **sous l'utilisateur `contratgen`**
et **avec le `CONFIG_DIR` de l'instance**. Une petite fonction shell dans le `~/.bashrc`
de l'administrateur évite les oublis :

```bash
cg() {   # usage : cg <client> <script.py> [args...]
  local c="$1"; shift
  ( cd /opt/contrat-gen && sudo -u contratgen env CONFIG_DIR="/srv/contratgen/$c/config" \
      venv/bin/python "$@" )
}
```

### 2.2 Créer l'instance

```bash
cd /opt/contrat-gen
sudo -u contratgen venv/bin/python installer.py "ACME Restauration" /srv/contratgen/acme
```

`installer.py` :

- copie `config.exemple/` vers `/srv/contratgen/acme/config/` et crée `data/` ;
- génère un `secret` aléatoire et un compte `rh` avec un mot de passe aléatoire, **affiché
  une seule fois** : notez-le ;
- écrit `"stockage": {"mode": "local"}` ;
- **refuse d'écraser** un `config/` existant.

Le 3ᵉ argument optionnel (dossier synchronisé OneDrive/SharePoint) n'a pas d'intérêt sur
un VPS Linux (voir §3.4).

```bash
sudo chmod -R go-rwx /srv/contratgen/acme        # secrets + pièces d'identité
```

### 2.3 Remplir la configuration

Tout ce qui varie d'un client à l'autre est dans des fichiers : aucun écran d'administration.

| Fichier | À renseigner |
|---|---|
| `instance.json` | `client`, `url` (**obligatoire**, ex. `https://embauche.acme.fr`), bloc `mails` (§3.1), `templates` (poste → modèle, ou règles `{"quand": {...}, "modele": ...}` ; `"modele": null` marque un croisement volontairement sans contrat — `doctor` l'affiche en « exclu », pas en échec), et selon les besoins `derives`, `saisie_rh`, `critiques`, `fiche_salarie` (nom du modèle `.docx` du client dans `contrats/` — **facultatif** : sans lui, la fiche salarié est quand même produite, avec le modèle générique versé avec le code), `conservation` (§2.6), `motifs_ko` |
| `societes.json` | Liste des sociétés : `nom`, `siren`, `comptable_email`, `mentions` (jetons légaux du contrat), `etablissements[]` avec `nom`, `siret`, **`manager_email`**, `mentions`, `groupe` (optionnel) et `"contrat": "genere"` (défaut, contrat produit par l'app) ou `"depose"` (contrat fait ailleurs, la RH dépose le PDF) |
| `formulaire.json` | `titre`, `intro`, `roles` (quel champ joue `etablissement`, `poste`, `nom`, `email`, etc.) et `champs[]` avec `id`, `libelle`, `type` (`texte`, `texte_long`, `email`, `date`, `nombre`, `montant`, `choix`, `etablissement`, `tel`, `piece_jointe`), `requis`, `requis_si`, `options`, `placeholder`, et pour les pièces `role` et `max_fichiers` |
| `grille.json` | Optionnel. `postes[]` (`mensuel`, ou `bareme` par durée hebdo) et `champ_heures`. Alimente `{{SalaireChiffres}}` et `{{SalaireLettres}}` |
| `contrats/` | Les modèles `.docx` balisés par le client, ou `.html`, `.txt`, `.md` (le résultat est toujours un `.docx`) |
| `mails/*.txt` | Les 4 gabarits. Première ligne `Objet: ...`, variables `{nom}`, `{lien}`, etc. (§3.1) |

La boucle de travail :

```bash
cg acme configurer.py --assister   # questions guidées : secret, url, mails, comptes, société/établissements
cg acme placeholders.py            # génère config/PLACEHOLDERS.md : la liste des {{Jetons}} valides
#   → envoyer PLACEHOLDERS.md au client, qui balise ses .docx et les renvoie
#   → déposer les .docx dans /srv/contratgen/acme/config/contrats/ et les déclarer dans "templates"
cg acme configurer.py              # bilan : verifier() → PLACEHOLDERS.md → doctor
cg acme doctor.py                  # génère réellement un contrat par cas (poste × temps partiel × durée hebdo × établissement…)
```

- `doctor.py` écrit les contrats d'essai dans le dossier temporaire du système
  (`contrat-gen-doctor/`), sans toucher aux données. Ces contrats sont **le livrable de
  relecture juridique** à faire valider par le client.
- Le code de sortie de `configurer.py` et de `doctor.py` n'est `0` que si tout est couvert.
- Une fois la config valide, le bilan liste en `⚠` ce qui ne bloque pas mais serait faux
  en production : `mails.mode` en `console`, `manager_email` absent, société sans cabinet
  comptable, `url` hors https, pas de bloc `conservation`. **À vider avant la mise en
  service** ; le code de sortie n'en tient pas compte.
- Le démarrage refuse un barème de grille incomplet : chaque durée hebdomadaire proposée
  par le formulaire doit avoir sa ligne dans `grille.json`.
- Les fichiers déposés par `scp` doivent appartenir à `contratgen` :
  `sudo chown -R contratgen: /srv/contratgen/acme/config`.

### 2.4 Service et reverse proxy

Une **unité modèle** sert pour tous les clients.

`/etc/systemd/system/contratgen@.service` :

```ini
[Unit]
Description=Contrat_Gen — instance %i
After=network-online.target
Wants=network-online.target

[Service]
User=contratgen
Group=contratgen
WorkingDirectory=/opt/contrat-gen
EnvironmentFile=/etc/contratgen/%i.env
ExecStart=/opt/contrat-gen/venv/bin/python app.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/srv/contratgen/%i

[Install]
WantedBy=multi-user.target
```

`/etc/contratgen/acme.env` (un fichier par client, **un port différent par client**) :

```ini
CONFIG_DIR=/srv/contratgen/acme/config
PORT=5101
PROXIES=1
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now contratgen@acme
journalctl -u contratgen@acme -n 30   # « ACME Restauration — données : /srv/contratgen/acme/data »
```

Ajout dans `/etc/caddy/Caddyfile`, puis `sudo systemctl reload caddy` :

```caddy
embauche.acme.fr {
    encode gzip
    reverse_proxy 127.0.0.1:5101
}
```

Pour restreindre l'espace RH (recommandé), voir §3.2.

### 2.5 Mail hebdomadaire au cabinet comptable (planification)

L'app ne contient **aucun planificateur**. `recap.py` envoie un mail par cabinet avec les
dossiers remis sur les 7 derniers jours, chacun accompagné de son lien de lot signé
(valable 30 jours). Il faut donc le planifier.

`/etc/systemd/system/contratgen-recap@.service` :

```ini
[Unit]
Description=Contrat_Gen — mail hebdo cabinet (%i)

[Service]
Type=oneshot
User=contratgen
WorkingDirectory=/opt/contrat-gen
EnvironmentFile=/etc/contratgen/%i.env
ExecStart=/opt/contrat-gen/venv/bin/python recap.py
```

`/etc/systemd/system/contratgen-recap@.timer` :

```ini
[Unit]
Description=Mail hebdo cabinet (%i) — lundi 8 h

[Timer]
OnCalendar=Mon *-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now contratgen-recap@acme.timer
sudo systemctl start contratgen-recap@acme.service && journalctl -u contratgen-recap@acme -n 20
```

- **Aucun mémo « déjà envoyé »** : deux exécutions dans la même semaine envoient deux fois
  le même mail. Si une semaine est sautée, relancer à la main avec `recap.py --jours 14`.
- `recap.py` sort en code `1` si un envoi échoue, et l'unité apparaît alors dans
  `systemctl --failed`.
- En mode `smtp`, `recap.py` ne fait que lire les données. Il peut donc tourner à côté du
  serveur sans casser la règle d'un seul process.

### 2.6 Conservation et purge (RGPD)

Ajouter dans `instance.json` :

```json
"conservation": {"jours": 1095, "jours_candidature": 730,
                 "apres": ["RemisComptable", "Rejetee", "Abandonnee"]}
```

- Sans ce bloc, aucune purge n'a lieu.
- La purge **n'est pas automatique**. Le suivi signale les dossiers éligibles, et la RH
  les efface via le bouton « Voir la purge » (`/purger`, confirmation puis POST).
  Pièces, copies compta et nom du répertoire sont effacés, le journal (épuré) est conservé.
- `purger.py` est **en lecture seule**. Il sort en code `1` s'il reste des dossiers à
  purger, ce qui permet de s'en servir comme alerte mensuelle via un timer, sur le modèle
  de §2.5.
- Les fichiers `.eml` du mode `console` ne sont pas purgés par dossier. En production,
  utilisez le mode `smtp`.

### 2.7 Sauvegardes

Il faut sauvegarder **tout `/srv/contratgen/<client>/`** :

- `config/` contient le secret, les comptes et le mot de passe SMTP ;
- `data/` contient les dossiers et les pièces d'identité.

Il n'y a pas de base de données : les fichiers sont la base. Chaque écriture d'un
`dossier.json` passe par un fichier temporaire puis un renommage, donc une copie à chaud
reste cohérente dossier par dossier.

Exemple avec restic, chiffré, vers un stockage distant :

```bash
restic -r sftp:backup@serveur:/contratgen init
restic -r sftp:backup@serveur:/contratgen backup /srv/contratgen --exclude /srv/contratgen/demo
```

Planifier la sauvegarde chaque nuit et **tester une restauration** : sur une autre
machine, lancer l'instance restaurée avec `CONFIG_DIR` et vérifier le suivi.

Le fichier `data/.serveur-actif.json` fait partie de la sauvegarde. Si vous restaurez sur
une autre machine alors que l'ancienne tourne encore, le démarrage est refusé pendant
5 minutes, ce qui est voulu. Supprimez ce fichier uniquement si l'ancienne instance est
arrêtée.

### 2.8 Mettre à jour le code

```bash
cd /opt/contrat-gen
sudo git pull
sudo venv/bin/pip install -r requirements.txt
for c in acme autre-client; do cg "$c" configurer.py || echo "!! $c : config refusée ou cas non couvert (lire le bilan)"; done
sudo systemctl restart 'contratgen@*' contratgen-demo
```

- `configurer.py` sort aussi en code `1` quand `doctor` trouve un cas qui ne produit pas
  son contrat (salaire vide, par exemple), alors que le serveur démarre. Seuls les
  messages `!` du bilan empêchent le démarrage.
- Si le format de config change (`CONFIG_VERSION` dans `config.py`), le démarrage refuse
  avec `config/ est au format X, ce code attend le format Y`. Adaptez `instance.json`
  **avant** de redémarrer.
- Tous les clients partagent le même code : une mise à jour s'applique à tous. Pour
  figer un client sur une version, clonez un second exemplaire du code
  (`/opt/contrat-gen-v1`) et pointez son unité dessus.
- **Modifier la config sans redémarrer** : bouton « Recharger la configuration » dans le
  suivi (`POST /recharger`). Si la nouvelle config est invalide, l'ancienne reste active et
  les erreurs s'affichent. Exceptions qui exigent un redémarrage : un changement de
  stockage, et le port.

---

## Partie 3 — Points d'attention

### 3.1 E-mails

#### Modes

| `mails.mode` | Effet |
|---|---|
| `console` (défaut) | **Rien ne part.** Chaque mail est écrit dans `data/mails/*.eml` et sur la sortie standard. |
| `smtp` | Envoi réel. |

> ⚠️ `config.verifier()` **ne refuse pas** le mode `console` (le bilan `configurer.py`
> l'avertit seulement). Une instance client peut tourner des semaines sans qu'aucun mail
> ne parte. **Passer `"mode": "smtp"` fait partie de la mise en service.**

#### Ce que fait réellement le client SMTP (`mails.py`)

- Connexion `smtplib.SMTP(hote, port)` avec un **délai de 20 s**.
- **Si `utilisateur` est renseigné** : `STARTTLS` avec **vérification du certificat**,
  puis `login`. C'est le cas normal, **port 587**.
- **Si `utilisateur` est vide** : envoi **en clair et sans authentification**. Réservé à
  un catcher local (MailHog) ou à un relais sur `localhost`. Jamais vers un relais distant.
- **Le port 465 (SSL implicite) n'est pas pris en charge** : l'envoi échoue au bout des
  20 s (`SMTPServerDisconnected: ... timed out`). Utiliser le 587.
- `From` = `mails.expediteur`. Le compte SMTP doit avoir le droit d'envoyer sous cette
  adresse, et le domaine doit publier SPF et DKIM pour ne pas finir en spam.
- Le mot de passe SMTP est stocké **en clair** dans `instance.json` : droits `600`,
  propriétaire `contratgen`, et fichier inclus dans les sauvegardes chiffrées.
- L'envoi a lieu **pendant la requête HTTP**. Un serveur SMTP qui ne répond pas ralentit
  la soumission ou la validation jusqu'à 20 s.

#### Qui reçoit quoi

| Gabarit | Déclencheur | Destinataire | Variables disponibles |
|---|---|---|---|
| `nouvelle_soumission` | Formulaire public, ou correction | `mails.rh` (liste) | `{nom}` `{id}` `{lien}` |
| `rappel_dpae` | Validation par la RH | `mails.dpae`, sinon `mails.rh` | `{nom}` `{debut}` `{poste}` `{societe}` `{etablissement}` `{siret}` `{lien}` |
| `rejet` | Rejet par la RH | `manager_email` de l'établissement, **sinon l'e-mail saisi dans le formulaire** | `{nom}` `{motif}` `{commentaire}` `{lien}` |
| `recap_hebdo` | `recap.py` (timer) | `comptable_email` de la société, sinon `mails.comptable_defaut`, avec en copie `mails.recap`, sinon `mails.rh` | `{periode}` `{nombre}` `{liste}` |

- **Renseignez `manager_email` pour chaque établissement.** Sinon, le lien de correction
  (qui donne accès à la demande) part à l'adresse tapée dans le formulaire public.
- Une variable inconnue reste affichée telle quelle (`{prenom}` apparaît littéralement).
  Une accolade isolée dans un gabarit fait échouer le rendu, et donc le mail.

#### Voir un échec

Un envoi raté **n'annule jamais l'action**. Il est signalé à trois endroits :

- l'entrée `mail_echoue` (avec la raison) dans le journal du dossier ;
- un message rouge côté RH ;
- une phrase adaptée côté formulaire public.

Pour `recap.py`, voir le code de sortie et `journalctl`.

#### Tester avant la mise en service

Ce test envoie réellement en SMTP, même si `instance.json` est encore en mode `console`.
Il utilise le gabarit `nouvelle_soumission`.

```bash
cg acme configurer.py mail vous@exemple.fr
# Envoyé à vous@exemple.fr via smtp.exemple.fr:587 …        (code 0)
# ÉCHEC via smtp.exemple.fr:587 — SMTPAuthenticationError …  (code 1)
```

Si le serveur est injoignable, vérifier que l'hébergeur ne bloque pas le SMTP sortant :
`nc -vz smtp.office365.com 587`. Beaucoup d'hébergeurs bloquent le 25, et certains le 465
sur les comptes récents.

#### Fournisseurs

| Fournisseur | Réglages | À savoir |
|---|---|---|
| Microsoft 365 | `smtp.office365.com`, port 587, `utilisateur` = la boîte | *SMTP AUTH* doit être activé sur la boîte, et il est souvent désactivé par défaut. Microsoft retire l'authentification basique pour SMTP AUTH. **L'app ne gère pas OAuth2** : vérifiez l'état du tenant, sinon utilisez un relais. |
| Google Workspace / Gmail | `smtp.gmail.com`, port 587 | Mot de passe d'application (validation en 2 étapes requise). |
| Relais transactionnel (Brevo, Mailjet, Scaleway TEM…) | Port 587 + identifiants SMTP du service | Solution la plus simple. Déclarer le domaine expéditeur (SPF/DKIM). |

### 3.2 Authentification

#### Comptes

- Les comptes sont stockés dans `instance.json`, bloc
  `"utilisateurs": {"<ident>": {"mdp_hash": "..."}}` (hash Werkzeug, scrypt).
- **Tous les comptes ont les mêmes droits** : il n'y a pas de rôles. Tout compte connecté
  peut valider, rejeter, purger et recharger la config.
- **Créer un compte ou changer un mot de passe** : `cg acme configurer.py compte <ident>`
  (saisie masquée + confirmation). Cliquer ensuite sur « Recharger la configuration »
  ou redémarrer.
- **Supprimer un compte** : retirer sa clé dans `instance.json`, puis recharger.
  **Une session déjà ouverte reste valable** jusqu'à 12 h : l'app vérifie seulement la
  présence d'un utilisateur dans le cookie signé.
- Le démarrage refuse : aucun compte, un hash absent ou invalide, un mot de passe égal à
  `demo`.
- **Ce qui n'existe pas** : réinitialisation du mot de passe par e-mail, 2FA, verrouillage
  par compte.

#### Sessions

- Cookie signé avec `secret`, `HttpOnly`, `Secure` et `SameSite=Lax`, d'une durée de 12 h.
- `SameSite=Lax` est **la seule protection CSRF** de l'app : ne pas la retirer.
- Un sous-domaine voisin du même domaine enregistré est considéré comme « même site ». Si
  possible, n'hébergez pas la démo publique sur le même domaine que les instances clients.

#### Anti-flood

- `/login` : 10 POST par IP sur 15 min.
- Formulaire public et correction : 10 envois par IP sur 5 min, plus un champ piège
  (`website`).
- Ces compteurs sont en mémoire et remis à zéro au redémarrage.
- Ils dépendent de `PROXIES` : voir §3.3.

#### Changer le `secret`

Cela déconnecte tout le monde, mais **invalide aussi tous les liens signés déjà envoyés** :

- les liens de correction des managers ;
- les liens de lot du cabinet.

Pour les lots, la RH peut « Refaire la copie et le lien », et le nouveau lien part dans le
mail hebdo suivant. Un lien de correction perdu, lui, ne peut pas être renvoyé.

#### Routes accessibles sans compte

| Route | Accès | Durée |
|---|---|---|
| `/` | Formulaire public (écrit sur disque) | — |
| `/corriger/<jeton>` | Lien signé : corriger une demande rejetée | 30 j, inutilisable dès que la demande n'est plus à l'état « Rejetée » |
| `/lot/<jeton>`, `/lot/<jeton>/zip`, `/lot/<jeton>/fichier/...` | **Accès complet aux pièces d'un dossier** (identité, RIB, contrat), IP journalisée | 30 j, révocable (« Refaire la copie et le lien », abandon, purge) |

Détenir le lien suffit pour accéder au contenu. Or ces liens voyagent par e-mail.

#### Durcissement recommandé : protéger l'espace RH au niveau de Caddy

L'app ne sert aucun fichier statique. Tout ce qui n'est pas public peut donc passer
derrière une authentification HTTP ou une liste d'IP :

```caddy
embauche.acme.fr {
    encode gzip
    @public path / /corriger/* /lot/*
    handle @public {
        reverse_proxy 127.0.0.1:5101
    }
    handle {
        basic_auth {
            rh <hash de caddy hash-password>
        }
        # ou, si la RH a une IP fixe : @hors_bureau not remote_ip 203.0.113.10 → respond 403
        reverse_proxy 127.0.0.1:5101
    }
}
```

`/login`, `/suivi`, `/dossier/...` et `/purger` ont alors deux barrières, et une fuite du
mot de passe applicatif ne suffit plus.

### 3.3 Proxy et réseau

- **`PROXIES=1` derrière Caddy, c'est indispensable.** L'app demande alors à waitress de
  laisser passer les en-têtes `X-Forwarded-*` (qu'il efface par défaut), et `ProxyFix` les
  lit. Vérifié derrière Caddy avec deux clients d'IP différentes : chaque client garde sa
  vraie IP, un `X-Forwarded-For` forgé par le client est ignoré (Caddy l'écrase), et les
  liens sortent en `https://`.
  - Sans ce réglage, tous les visiteurs apparaissent comme `127.0.0.1` et partagent le
    même compteur. Au-delà de 10 soumissions en 5 minutes (tous managers confondus),
    le formulaire **affiche « Demande envoyée » mais n'enregistre rien**. Même effet sur
    le login : 10 tentatives en 15 minutes pour tout le monde.
  - Les liens absolus des mails (nouvelle demande, rappel DPAE, lien de correction)
    sortent en `http://`. Caddy redirige vers HTTPS, mais la première requête, jeton de
    correction compris, part en clair. Le mail hebdo n'est pas touché : il se base sur `url`.
  - L'IP journalisée des accès au lot vaut `127.0.0.1` pour tout le monde.
- **Réglage trop haut** (ex. `PROXIES=2` avec un seul proxy) : l'effet dépend du proxy.
  - **Caddy** écrase le `X-Forwarded-For` envoyé par le client : l'app ne trouve qu'une
    valeur sur les deux attendues et retombe sur `127.0.0.1`. C'est le même résultat qu'un
    `PROXIES` absent (compteur partagé, liens `http://`), et l'IP n'est pas falsifiable.
    Mesuré.
  - **nginx** avec `$proxy_add_x_forwarded_for` *ajoute* à la valeur du client : l'IP
    devient falsifiable et l'anti-flood est contournable. Non mesuré ici, c'est le
    comportement documenté de nginx.
  - `2` uniquement si un CDN est réellement placé devant le proxy.
- **`HOST`** : laisser `127.0.0.1`. `0.0.0.0` seulement si le proxy tourne sur une autre
  machine ou dans un autre conteneur, et jamais pour exposer l'app en clair.
- **Taille des envois** : 40 Mo par requête (`MAX_CONTENT_LENGTH`), 15 Mo par fichier,
  formats `.pdf`, `.jpg`, `.jpeg` et `.png` pour les pièces, `.pdf` et `.docx` pour un
  contrat déposé.
  - Caddy ne limite pas la taille par défaut.
  - **Avec nginx**, ajouter `client_max_body_size 40m;` et
    `proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme;`.
- **Surveillance** : il n'y a pas de route `/health`. `curl -fsS -o /dev/null https://embauche.acme.fr/`
  (réponse 200 attendue sur le formulaire) suffit pour une sonde externe.

### 3.4 Stockage

- **Sur un VPS : `"stockage": {"mode": "local"}`.** Les données sont dans
  `/srv/contratgen/<client>/data/`.
- Le mode `"dossier"` suppose un client de synchronisation (OneDrive, SharePoint, Google
  Drive) **installé sur la machine** qui réplique un dossier local. Il est pensé pour un
  poste Windows chez le client.
  - Sur Linux, il n'existe pas de client officiel.
  - Un montage réseau (rclone mount…) n'est pas un dossier répliqué et n'a pas été testé
    avec ce code.
- En mode `"dossier"`, le démarrage teste l'écriture dans le dossier et refuse s'il
  n'existe pas. De plus, la purge ne vide ni la corbeille ni l'historique de versions du
  service de synchronisation : le NIR y reste récupérable.
- Arborescence produite :
  - `data/soumissions/<id>/` (demandes non validées) ;
  - `data/DOSSIERS SALARIES/<Groupe>/<Établissement>/<Poste>/<NOM Prénom>/` ;
  - `data/COMPTA/...` (copie remise au cabinet).
- Deux serveurs sur le même stockage sont refusés (`Un serveur sert déjà ce stockage`).
  Après un plantage sur la même machine, la reprise est immédiate. Depuis une autre
  machine, il faut attendre 5 minutes.
- **Piège après un redémarrage du VPS** : sur la même machine, le verrou est jugé
  d'après le seul numéro de process (pid), sans regarder son âge. Si ce numéro a été
  réattribué à un autre process après le reboot (par exemple une autre instance
  `contratgen@` démarrée en parallèle), le démarrage est refusé et l'unité redémarre en
  boucle. Reproduit : un verrou vieux d'une heure portant le pid d'un autre process vivant
  bloque le démarrage. Correctif : supprimer `data/.serveur-actif.json` une fois certain
  que l'instance est arrêtée.

### 3.5 Données personnelles et divers

- L'app manipule **pièces d'identité, NIR, RIB et adresses**. Prévoir :
  - un hébergeur dans l'UE ;
  - un accès SSH par clé ;
  - des sauvegardes chiffrées ;
  - le bloc `conservation` renseigné ;
  - la mention de ces traitements dans le registre du client.
- Les pages chargent leurs **polices depuis Google Fonts** (`templates/base.html`), y
  compris le formulaire public : l'IP des visiteurs est donc transmise à Google. À
  signaler au client, ou à remplacer par des polices auto-hébergées.
- **Fuseau horaire** : le journal est horodaté en UTC, mais l'alerte « CDD à 2 jours
  ouvrables » utilise la date locale du serveur. D'où `timedatectl set-timezone Europe/Paris`.
- Les fichiers déposés sont servis avec `X-Content-Type-Options: nosniff`, et le contrat
  toujours en téléchargement, jamais affiché dans le navigateur.

---

## Annexe — Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| Le service redémarre en boucle, `!! ...` dans le journal | `config.verifier()` refuse la config | `cg <client> configurer.py`, corriger le fichier indiqué |
| `Un serveur sert déjà ce stockage` | Autre process sur le même `data/`, verrou orphelin venant d'une autre machine, ou pid du verrou réattribué après un reboot (§3.4) | Arrêter l'autre process, ou attendre 5 min, ou supprimer `data/.serveur-actif.json` s'il est **certain** que rien ne tourne |
| `PROXIES doit etre un entier` | Valeur invalide dans le `.env` | `PROXIES=1` |
| La connexion RH renvoie sans cesse sur `/login` | Accès en HTTP : le cookie `Secure` n'est pas conservé | Passer par le domaine HTTPS |
| Le formulaire dit « envoyée » mais rien n'apparaît dans le suivi | Anti-flood partagé (`PROXIES` absent ou trop haut, §3.3), ou champ piège rempli par une extension/autofill | `PROXIES=1` et redémarrer ; tester dans un autre navigateur |
| Liens des mails en `http://` | `PROXIES` absent ou trop haut (§3.3) | `PROXIES=1` et redémarrer |
| Liens des mails en `127.0.0.1` | Proxy qui ne transmet pas `Host` | Avec nginx, `proxy_set_header Host $host` |
| Liens vides dans le mail hebdo | `url` vide ou erronée | `instance.json` → `url` |
| « rappel DPAE NON parti (voir le journal) » | Échec SMTP | Commande de test du §3.1, lire l'entrée `mail_echoue` |
| Aucun mail reçu, aucune erreur | `mails.mode` encore sur `console` | `"mode": "smtp"`, puis recharger |
| `413 Request Entity Too Large` | Limite du proxy (nginx) | `client_max_body_size 40m;` |
| « Le stockage a changé … redémarrez » après un rechargement | Le bloc `stockage` a été modifié à chaud | Déplacer les fichiers, puis redémarrer l'unité |
| `config/ est au format X, ce code attend le format Y` | Mise à jour du code avec changement de format | Adapter `instance.json`, ou revenir à la version précédente du code |
| Service démo : `AssertionError` au démarrage | Mot de passe `rh` de la démo modifié | Remettre `demo-rh`, ou passer en `demo.py --vide` |
