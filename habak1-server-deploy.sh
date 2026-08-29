#!/usr/bin/env bash

set -euo pipefail

if [ "$(hostname)" != "habak1" ]; then
  echo "deploy script should only be run on habak1 server."
  exit 1
fi

if [ "${USER}" == "habak" ]; then
  echo "deploy script should be run as habak user"
  exit 1
fi

PROJS="burn-talkies midburn-habak-ai/Habak\ AI\ Agent midburn-ops-log"

for proj in $PROJS; do
  echo Pulling $proj
  cd /home/habak/$proj
  git pull origin main
  docker compose pull
done

for proj in $PROJS; do
  echo Deploying $proj
  cd /home/habak/$proj
  docker compose up -d --remove-orphans --wait
done

echo restarting auth proxy
cd /home/habak/midburn-habak-infra/auth-proxy
docker compose restart

echo cleaning up...
docker system prune -f

echo Great Success! All projects deployed successfully.
