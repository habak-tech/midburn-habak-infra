# midburn-habak-infra

A public repo for managing the infrastructure of the Midburn Control Room, also known as Habak.

**WARNING**

**The repotisory is public, do not commit any sensitive information**

The repository contains scripts, configurations, and documentation to help set up and maintain the necessary systems
for the control room operations.

The authentication proxy supports either separate hostnames or a shared HTTPS
IP/hostname with application path prefixes. See the
[auth proxy deployment instructions](auth-proxy/README.md#shared-ip--path-routing)
for `/ops`, `/talkies`, `/ai`, and `/auth` setup across the sibling repositories.
