#!/usr/bin/env bash

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root."
  exit 1
fi

# check if `habak` user exists with home directory /home/habak, if not create it
echo habak user...
if ! id -u habak >/dev/null 2>&1; then
  echo "Creating user 'habak' with home directory /home/habak"
  useradd -m -d /home/habak -s /bin/bash habak
fi

# check if docker is installed, if not install it
echo docker...
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  apt update
  apt install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt update
  apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

usermod -aG docker habak

echo ssh keys...
for repo in burn_talkies ops_log habak_infra habak_ai; do
  if [ ! -f "/home/habak/.ssh/id_ed25519_${repo}" ]; then
    echo "Generating SSH key for ${repo}..."
    sudo -u habak ssh-keygen -t ed25519 -f "/home/habak/.ssh/id_ed25519_${repo}" -N ""
  fi
done

echo ssh config...
cat <<EOF | sudo -u habak tee /home/habak/.ssh/config >/dev/null
Host github.com-burn-talkies
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_burn_talkies

Host github.com-ops-log
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_ops_log

Host github.com-habak-infra
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_habak_infra

Host github.com-habak-ai
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_habak_ai
EOF

echo cloning repos...
rm -rf /home/habak/burn-talkies /home/habak/midburn-ops-log /home/habak/midburn-habak-infra /home/habak/midburn-habak-ai
for repo in burn-talkies/habak-tech/burn-talkies ops-log/habak-tech/midburn-ops-log habak-infra/habak-tech/midburn-habak-infra habak-ai/habak-tech/midburn-habak-ai; do
  sudo -u habak bash -c "cd && git clone ssh://github.com-${repo}.git"
done

echo setup auth proxy...
docker network create midburn-habak-auth-proxy || true
sudo -u habak bash -ic "cd ~/midburn-habak-infra/auth-proxy && mkdir -p authelia/secrets"
for secret in JWT_SECRET SESSION_SECRET STORAGE_ENCRYPTION_KEY; do
  cp -f /etc/habak/secrets/authelia/${secret} /home/habak/midburn-habak-infra/auth-proxy/authelia/secrets/${secret}
  chmod 600 /home/habak/midburn-habak-infra/auth-proxy/authelia/secrets/${secret}
done
cp -f /etc/habak/secrets/authelia/users.yml /home/habak/midburn-habak-infra/auth-proxy/authelia/users.yml

echo setup .envs...
cp -f /etc/habak/secrets/auth-proxy.env /home/habak/midburn-habak-infra/auth-proxy/.env
cp -f /etc/habak/secrets/midburn-ops-log.env /home/habak/midburn-ops-log/.env
cp -f /etc/habak/secrets/burn-talkies.env /home/habak/burn-talkies/.env
cp -f /etc/habak/secrets/midburn-habak-ai.env /home/habak/midburn-habak-ai/Habak\ AI\ Agent/.env

echo fix ownership...
chown -R habak:habak /home/habak

echo setup complete, run deploy to start the services
