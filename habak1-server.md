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