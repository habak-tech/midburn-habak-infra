# How to generate secrets

Generate password hashes:

```
docker run --rm -it \
  authelia/authelia:latest \
  authelia crypto hash generate argon2
```

Populate the secrets directory:

```
cd auth-proxy
mkdir -p authelia/secrets

docker run --rm authelia/authelia:latest \
  authelia crypto rand --length 64 \
  > authelia/secrets/JWT_SECRET

docker run --rm authelia/authelia:latest \
  authelia crypto rand --length 64 \
  > authelia/secrets/SESSION_SECRET

docker run --rm authelia/authelia:latest \
  authelia crypto rand --length 64 \
  > authelia/secrets/STORAGE_ENCRYPTION_KEY

chmod 600 authelia/secrets/*
```
