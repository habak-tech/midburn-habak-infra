#!/usr/bin/env bash

set -euo pipefail

if [ "$(hostname)" != "habak1" ]; then
  echo "deploy script should only be run on habak1 server."
  exit 1
fi

if [ "${USER}" != "habak" ]; then
  echo "deploy script should be run as habak user"
  exit 1
fi

PROJS="burn-talkies midburn-habak-ai midburn-ops-log"

for proj in $PROJS; do
  echo Pulling $proj
  cd /home/habak/$proj
  if [ "${proj}" == "midburn-habak-ai" ]; then
    cd "Habak AI Agent"
  fi
  git pull origin main
  docker compose pull
done

for proj in $PROJS; do
  echo Deploying $proj
  cd /home/habak/$proj
  if [ "${proj}" == "midburn-habak-ai" ]; then
    cd "Habak AI Agent"
  fi
  docker compose up -d --remove-orphans --wait
done

echo restarting auth proxy
cd /home/habak/midburn-habak-infra/auth-proxy
docker compose restart

echo cleaning up...
docker system prune -f --all

echo Great Success! All projects deployed successfully.
