#!/bin/bash
# Deploie une version de Contrat_Gen (instance wingstop) depuis ghcr.io.
# Installe sur le VPS en /usr/local/bin/deployer-contratgen (root:root 755).
# Appele par GitHub Actions en SSH, via la commande forcee de la cle du compte
# « deploiement » : le seul argument accepte est un tag vX.Y.Z. Si le
# conteneur n'est pas sain 150 s apres le demarrage, on revient a la version
# precedente.
set -euo pipefail

DIR=/opt/contratgen-docker
IMAGE=ghcr.io/kevint78/contrat_gen
CONTENEUR=contratgen-wingstop

TAG="${1:-}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "tag refusé : « $TAG » (attendu : vX.Y.Z)"; exit 2; }

exec 9>/run/lock/deployer-contratgen.lock
flock -n 9 || { echo "un déploiement est déjà en cours"; exit 3; }

cd "$DIR"
PRECEDENT=$(sed -n 's/^IMAGE_TAG=//p' .env)
echo "== déploiement : ${PRECEDENT:-aucune version} -> $TAG"

docker pull --quiet "$IMAGE:$TAG"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" .env
docker compose up -d

for _ in $(seq 1 30); do
  etat=$(docker inspect -f '{{.State.Health.Status}}' "$CONTENEUR" 2>/dev/null || echo absent)
  if [ "$etat" = healthy ]; then
    echo "$(date -Is) ${PRECEDENT:-} -> $TAG OK" >> historique.log
    echo "== $TAG en service"
    exit 0
  fi
  sleep 5
done

echo "!! $TAG n'est pas sain (état : $etat). Derniers journaux :"
docker compose logs --no-color --tail 40 wingstop || true
echo "$(date -Is) ${PRECEDENT:-} -> $TAG ECHEC" >> historique.log
if [ -n "$PRECEDENT" ]; then
  echo "== retour à $PRECEDENT"
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PRECEDENT/" .env
  docker compose up -d
fi
exit 1
