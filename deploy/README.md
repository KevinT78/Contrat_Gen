# Déploiement automatique de Contrat_Gen

Un push sur une branche de déploiement la met en ligne :

| Branche | Environnement | Adresse | Port VPS | Base Atlas |
|---|---|---|---|---|
| `master` | production | https://wingstop-rh.wingstop-flavorcard.fr | 127.0.0.1:5102 | `wingstop` |
| `preprod` | préproduction | https://preprod-rh.wingstop-flavorcard.fr | 127.0.0.1:5103 | `wingstop_preprod` |

```
git push origin preprod      (ou master)
   └─► GitHub Actions (.github/workflows/deploiement.yml)
         1. tests          python tests/test_*.py
         2. image          ghcr.io/kevint78/contrat_gen:<sha du commit>
         3. déploiement    ssh deploiement@VPS "preprod <sha>"
                              └─► /usr/local/bin/deployer-contratgen preprod <sha>
                                    pull → docker compose up -d → attend « healthy »
                                    sinon : retour automatique à la version précédente
```

Habitude conseillée : pousser d'abord sur `preprod`, vérifier sur le lien de préprod,
puis fusionner `preprod` dans `master`.

La préprod a sa propre base (vide de toute donnée personnelle : seules les sociétés
sont copiées) et n'envoie aucun mail (`MAILS_MODE=console`). Les conteneurs n'écoutent
que sur 127.0.0.1 ; Caddy reste la seule porte d'entrée. La config du client est montée
en lecture seule, les secrets viennent de `/etc/contratgen/*.env` : ni l'un ni l'autre
n'entre dans l'image (publique, comme le dépôt).

## Revenir à une version précédente

Sur le VPS : `sudo deployer-contratgen prod <sha>` (sha d'un commit déjà publié).
Historique : `/opt/contratgen-docker/historique.log`.

## Mise en place sur le VPS (une seule fois)

Toutes les commandes en root (`sudo -i`), depuis un dossier qui contient `deploy/`
(par exemple `/opt/contrat-gen` après `git fetch && git checkout origin/master -- deploy`).
L'ancien service `contratgen@wingstop` (port 5101) continue de tourner pendant toute la
mise en place.

1. **Dossier du déploiement et script**

   ```bash
   install -d -m 750 /opt/contratgen-docker
   install -m 644 deploy/compose.yml /opt/contratgen-docker/compose.yml
   install -m 755 deploy/deployer.sh /usr/local/bin/deployer-contratgen
   U=$(id -u contratgen); G=$(id -g contratgen)
   cat > /opt/contratgen-docker/prod.env <<EOF
   ENVIRONNEMENT=prod
   IMAGE_TAG=
   CG_UID=$U
   CG_GID=$G
   ENV_FILE=/etc/contratgen/wingstop.env
   CONFIG_HOTE=/srv/contratgen/wingstop/config
   PORT_HOTE=5102
   EOF
   cat > /opt/contratgen-docker/preprod.env <<EOF
   ENVIRONNEMENT=preprod
   IMAGE_TAG=
   CG_UID=$U
   CG_GID=$G
   ENV_FILE=/etc/contratgen/preprod.env
   CONFIG_HOTE=/srv/contratgen/preprod/config
   PORT_HOTE=5103
   EOF
   docker compose version     # le plugin compose doit répondre
   ```

2. **Instance de préprod** : config copiée de la prod, base neuve, mails coupés

   ```bash
   install -d -m 700 -o contratgen -g contratgen /srv/contratgen/preprod
   cp -a /srv/contratgen/wingstop/config /srv/contratgen/preprod/config
   # dans /srv/contratgen/preprod/config/instance.json : "url" -> https://preprod-rh.wingstop-flavorcard.fr
   nano /srv/contratgen/preprod/config/instance.json

   # secrets : mêmes accès Atlas, autre base, autre clé de session, aucun mail envoyé
   grep -vE '^(MONGO_DB|SECRET_KEY|MAILS_MODE|PORT|CONFIG_DIR)=' /etc/contratgen/wingstop.env > /etc/contratgen/preprod.env
   { echo "MONGO_DB=wingstop_preprod"
     echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
     echo "MAILS_MODE=console"; } >> /etc/contratgen/preprod.env
   chmod 600 /etc/contratgen/preprod.env

   # base neuve : sociétés copiées de la prod (lue seulement), compte rh neuf
   /opt/contrat-gen/venv/bin/python deploy/init_preprod.py /etc/contratgen/wingstop.env /etc/contratgen/preprod.env
   ```

   Noter le mot de passe rh affiché : il n'est montré qu'une fois.

3. **Compte `deploiement`**, qui ne peut lancer que le script de déploiement

   ```bash
   useradd --create-home --shell /bin/bash deploiement     # sans mot de passe : connexion par clé seulement
   echo 'deploiement ALL=(root) NOPASSWD: /usr/local/bin/deployer-contratgen' > /etc/sudoers.d/deploiement
   chmod 440 /etc/sudoers.d/deploiement && visudo -cf /etc/sudoers.d/deploiement
   ```

4. **Clé SSH réservée à GitHub**, générée sur le poste (PowerShell), jamais réutilisée ailleurs :

   ```powershell
   ssh-keygen -t ed25519 -N '""' -C github-actions-contratgen -f $HOME\deploiement-github
   ```

   Sur le VPS, la clé publique (`deploiement-github.pub`) n'ouvre que le script :

   ```bash
   install -d -m 700 -o deploiement -g deploiement /home/deploiement/.ssh
   echo 'restrict,command="sudo /usr/local/bin/deployer-contratgen \"$SSH_ORIGINAL_COMMAND\"" CONTENU_DE_deploiement-github.pub' \
     > /home/deploiement/.ssh/authorized_keys
   chown deploiement: /home/deploiement/.ssh/authorized_keys && chmod 600 /home/deploiement/.ssh/authorized_keys
   ```

5. **Empreinte du serveur** (pour que GitHub vérifie qu'il parle au bon VPS) :

   ```bash
   awk '{print "51.254.218.35", $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
   ```

6. **Secrets GitHub** — droits *admin* du dépôt requis. Settings → Secrets and variables →
   Actions → New repository secret :

   | Secret | Valeur |
   |---|---|
   | `VPS_HOTE` | `51.254.218.35` |
   | `VPS_SSH_CLE` | contenu de `deploiement-github` (la clé **privée**) |
   | `VPS_HOTE_CONNU` | la ligne affichée à l'étape 5 |

   Ensuite, supprimer la clé privée du poste. Tant que ces secrets manquent, le workflow
   publie l'image mais saute le déploiement (avertissement dans Actions).

7. **DNS** (zone OVH de wingstop-flavorcard.fr) : enregistrement `A` `preprod-rh` →
   `51.254.218.35` (et `AAAA` → l'IPv6 du VPS si les autres sous-domaines en ont une).

8. **Caddy** : ajouter le site de préprod dans `/etc/caddy/Caddyfile`, puis
   `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && systemctl reload caddy` :

   ```
   preprod-rh.wingstop-flavorcard.fr {
       encode gzip
       header X-Robots-Tag "noindex, nofollow"
       reverse_proxy 127.0.0.1:5103
   }
   ```

9. **Premier déploiement** : pousser sur `preprod`, suivre l'onglet Actions. Au premier
   passage, le paquet `contrat_gen` est créé sur ghcr.io : s'il est privé, le VPS ne peut
   pas le télécharger. Le rendre public (Packages → contrat_gen → Package settings →
   Change visibility) et relancer le job « deploiement ». Vérifier :

   ```bash
   docker ps --filter name=contratgen-          # STATUS : (healthy)
   curl -sI http://127.0.0.1:5103/login | head -1
   ```

10. **Production** : fusionner dans `master` (le conteneur prod démarre sur 5102, à côté de
    l'ancien service), vérifier `curl -sI http://127.0.0.1:5102/login`, puis basculer Caddy :
    bloc `wingstop-rh.…`, `reverse_proxy 127.0.0.1:5101` → `127.0.0.1:5102`, valider,
    recharger. Retour arrière : remettre 5101 et recharger. L'ancien service reste démarré
    tant que la nouvelle version n'a pas fait ses preuves ; ensuite seulement
    `systemctl disable --now contratgen@wingstop`.
