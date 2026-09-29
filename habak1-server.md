# habak1 server

It's a Raspberry Pi 4 that is the main server for Habak.

## Setup

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to flash the latest Raspberry Pi OS Lite (64-bit) 
to a microSD card. At time of initial setup it was based on Debian 13.5 (Trixie).
In the imager, select to enable SSH, set username, password and public key, set hostname to `habak1`, set timezone to Jerusalem,
keyboard layout `us`, and enable Wi-Fi.

Setup SSH access on your PC:

```
HABAK1_IP=
HABAK1_USER=
mkdir -p /etc/midburn
cat <<EOF > /etc/midburn/habak.ssh-config
Host midburn-habak1
    HostName $HABAK1_IP
    User $HABAK1_USER
EOF
echo "Include /etc/midburn/habak.ssh-config" >> ~/.ssh/config
```

[Install Docker](https://docs.docker.com/engine/install/debian/) on the server:

```
# Add Docker's official GPG key:
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Give your user access to Docker:

```
sudo usermod -aG docker $USER
```

Relogin to the server for the group change to take effect.

Generate ssh keys for each repo:

```
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_burn_talkies
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_ops_log
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_habak_infra
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_habak_ai
```

Add each public key to the relevant GitHub repo as a deploy key with read/write access.

Setup ssh config for each repo:

```
cat <<EOF >> ~/.ssh/config
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
```

Clone the repositories:

```
cd
git clone ssh://github.com-burn-talkies/habak-tech/burn-talkies.git
git clone ssh://github.com-ops-log/habak-tech/midburn-ops-log.git
git clone ssh://github.com-habak-infra/habak-tech/midburn-habak-infra.git
git clone ssh://github.com-habak-ai/habak-tech/midburn-habak-ai.git
```

see auth-proxy/README.md for setup of the auth proxy and related repos

Setup backup USB disk, the USB disk is connected to the Raspberry Pi USB port

```
sudo mkdir -p /mnt/backup
cat <<EOF | sudo tee -a /etc/fstab
UUID=27FD-DC76  /mnt/backup  vfat  defaults,nofail,x-systemd.device-timeout=15s,uid=1000,gid=1000  0  2
EOF
sudo systemctl daemon-reload
sudo mount -a
```

Setup gcloud CLI

```
wget https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-arm.tar.gz
tar -xf google-cloud-cli-linux-arm.tar.gz
./google-cloud-sdk/install.sh
rm google-cloud-cli-linux-arm.tar.gz
```

Create a service account key json for the midburn-habak1-server service account, and copy it to the server at ~/.gcloud-service-account.json

```
gcloud auth activate-service-account --key-file ~/.gcloud-service-account.json
```

## Deploy

Push changes to github, wait for the CI/CD pipeline to complete, then run deploy script on the habak1 server:

```
midburn-habak-infra/habak1-server-deploy.sh
```
