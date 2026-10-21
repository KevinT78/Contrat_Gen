#!/bin/bash
# Deploie une version de Contrat_Gen depuis ghcr.io, en prod ou en preprod.
#   deployer-contratgen prod    <sha du commit>
#   deployer-contratgen preprod <sha du commit>
# Installe sur le VPS en /usr/local/bin/deployer-contratgen (root:root 755).
# Appele par GitHub Actions en SSH, via la commande forcee de la cle du compte
# « deploiement » : les deux mots arrivent alors en un seul argument. Si le
# conteneur n'est pas sain 150 s apres le demarrage, on revient a la version
# precedente.
set -euo pipefail

DIR=/opt/contratgen-docker
IMAGE=ghcr.io/kevint78/contrat_gen

ARGS=("$@")
[ $# -eq 1 ] && read -r -a ARGS <<< "$1"
CIBLE="${ARGS[0]:-}"
TAG="${ARGS[1]:-}"
case "$CIBLE" in
  prod|preprod) ;;
  *) echo "environnement refusé : « $CIBLE » (attendu : prod ou preprod)"; exit 2 ;;
esac
[[ "$TAG" =~ ^[0-9a-f]{40}$ ]] || { echo "version refusée : « $TAG » (attendu : sha complet du commit)"; exit 2; }

exec 9>"/run/lock/deployer-contratgen-$CIBLE.lock"
flock -n 9 || { echo "un déploiement $CIBLE est déjà en cours"; exit 3; }

cd "$DIR"
VARS="$DIR/$CIBLE.env"
CONTENEUR="contratgen-$CIBLE"
compose() { docker compose -p "contratgen-$CIBLE" -f "$DIR/compose.yml" --env-file "$VARS" "$@"; }

PRECEDENT=$(sed -n 's/^IMAGE_TAG=//p' "$VARS")
echo "== $CIBLE : ${PRECEDENT:-aucune version} -> $TAG"

docker pull --quiet "$IMAGE:$TAG"
sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" "$VARS"
compose up -d

for _ in $(seq 1 30); do
  etat=$(docker inspect -f '{{.State.Health.Status}}' "$CONTENEUR" 2>/dev/null || echo absent)
  if [ "$etat" = healthy ]; then
    echo "$(date -Is) $CIBLE ${PRECEDENT:-} -> $TAG OK" >> historique.log
    echo "== $CIBLE : $TAG en service"
    exit 0
  fi
  sleep 5
done

echo "!! $TAG n'est pas sain en $CIBLE (état : $etat). Derniers journaux :"
compose logs --no-color --tail 40 app || true
echo "$(date -Is) $CIBLE ${PRECEDENT:-} -> $TAG ECHEC" >> historique.log
if [ -n "$PRECEDENT" ]; then
  echo "== retour à $PRECEDENT"
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$PRECEDENT/" "$VARS"
  compose up -d
fi
exit 1
