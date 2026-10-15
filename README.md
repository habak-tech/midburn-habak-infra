# midburn-habak-infra

A public repo for managing the infrastructure of the Midburn Control Room, also known as Habak.

**WARNING**

**The repotisory is public, do not commit any sensitive information**

The repository contains scripts, configurations, and documentation to help set up and maintain the necessary systems
for the control room operations.

The authentication proxy serves all applications from one HTTPS origin with
fixed paths: `/ops`, `/talkies`, `/ai`, and `/auth`. See the
[auth proxy deployment instructions](auth-proxy/README.md) for setup.

The local deployment on `ori-lp` is documented in [habak-vm.md](habak-vm.md).
