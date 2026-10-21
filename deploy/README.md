# Déploiement automatique de Contrat_Gen

Un tag `vX.Y.Z` poussé sur GitHub met l'instance « wingstop » en production :

```
git tag v1.0.0 && git push origin v1.0.0
   └─► GitHub Actions (.github/workflows/deploiement.yml)
         1. tests          python tests/test_*.py
         2. image          ghcr.io/kevint78/contrat_gen:v1.0.0
         3. production     ssh deploiement@VPS v1.0.0
                              └─► /usr/local/bin/deployer-contratgen v1.0.0
                                    pull → docker compose up -d → attend « healthy »
                                    sinon : retour automatique à la version précédente
```

Le conteneur écoute sur `127.0.0.1:5102` ; Caddy reste la seule porte d'entrée.
La config du client est montée en lecture seule depuis `/srv/contratgen/wingstop/config`,
les secrets viennent de `/etc/contratgen/wingstop.env` : ni l'un ni l'autre n'entre dans
l'image (publique, comme le dépôt).

## Revenir à une version précédente

Sur le VPS : `sudo deployer-contratgen v1.0.0` (n'importe quel tag déjà publié).
Historique : `/opt/contratgen-docker/historique.log`.

## Mise en place sur le VPS (une seule fois)

Toutes les commandes en root (`sudo -i`). L'ancien service `contratgen@wingstop` (port
5101) continue de tourner pendant toute la mise en place.

1. **Dossier du déploiement**

   ```bash
   install -d -m 750 /opt/contratgen-docker
   install -m 644 compose.yml /opt/contratgen-docker/compose.yml
   cat > /opt/contratgen-docker/.env <<EOF
   IMAGE_TAG=
   CG_UID=$(id -u contratgen)
   CG_GID=$(id -g contratgen)
   PORT_HOTE=5102
   EOF
   install -m 755 deployer.sh /usr/local/bin/deployer-contratgen
   docker compose version     # le plugin compose doit répondre
   ```

2. **Compte `deploiement`**, qui ne peut lancer que le script de déploiement

   ```bash
   useradd --create-home --shell /bin/bash deploiement     # sans mot de passe : connexion par clé seulement
   echo 'deploiement ALL=(root) NOPASSWD: /usr/local/bin/deployer-contratgen' > /etc/sudoers.d/deploiement
   chmod 440 /etc/sudoers.d/deploiement && visudo -cf /etc/sudoers.d/deploiement
   ```

3. **Clé SSH réservée à GitHub**, générée sur le poste (PowerShell), jamais réutilisée ailleurs :

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

4. **Empreinte du serveur** (pour que GitHub vérifie qu'il parle au bon VPS) :

   ```bash
   awk '{print "51.254.218.35", $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
   ```

5. **Secrets GitHub** — droits *admin* du dépôt requis. Settings → Environments → créer
   `production`, puis y ajouter :

   | Secret | Valeur |
   |---|---|
   | `VPS_HOTE` | `51.254.218.35` |
   | `VPS_SSH_CLE` | contenu de `deploiement-github` (la clé **privée**) |
   | `VPS_HOTE_CONNU` | la ligne affichée à l'étape 4 |

   Ensuite, supprimer la clé privée du poste.

6. **Premier tag** : `git tag v1.0.0 && git push origin v1.0.0`, puis suivre l'onglet
   Actions. Au premier passage, le paquet `contrat_gen` est créé sur ghcr.io : s'il est
   privé, le VPS ne peut pas le télécharger. Le rendre public (Packages → contrat_gen →
   Package settings → Change visibility) et relancer le job « production ».

7. **Vérifier avant de basculer** (sur le VPS) :

   ```bash
   docker ps --filter name=contratgen-wingstop      # STATUS : (healthy)
   curl -sI http://127.0.0.1:5102/login | head -1  # HTTP/1.1 200 OK
   docker logs --tail 20 contratgen-wingstop        # « base mongo : wingstop »
   ```

8. **Basculer le trafic** : dans `/etc/caddy/Caddyfile`, bloc `wingstop-rh.…`,
   `reverse_proxy 127.0.0.1:5101` → `127.0.0.1:5102`, puis
   `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && systemctl reload caddy`.
   Retour arrière : remettre 5101 et recharger. L'ancien service reste démarré tant que la
   nouvelle version n'a pas fait ses preuves ; ensuite seulement
   `systemctl disable --now contratgen@wingstop`.
