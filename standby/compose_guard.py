#!/usr/bin/env python3
"""Run standby Compose with crash retries but no Docker-daemon boot autostart."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECTS = {"burn-talkies", "midburn-ops-log", "habak-ai", "auth-proxy"}


def protect_existing():
    ids = subprocess.check_output(["docker", "ps", "--all", "--quiet", "--filter",
        "label=com.docker.compose.project"], text=True).split()
    if not ids:
        return
    containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
    update = []
    for container in containers:
        labels = container.get("Config", {}).get("Labels") or {}
        host = container.get("HostConfig", {})
        if (labels.get("com.docker.compose.project") in PROJECTS and not host.get("AutoRemove") and
                host.get("RestartPolicy", {}).get("Name") in ("always", "unless-stopped")):
            update.append(container["Id"])
    if update:
        subprocess.run(["docker", "update", "--restart=on-failure:5", *update], check=True)


def compose(args):
    mutation = next((index for index, arg in enumerate(args) if arg in ("up", "create", "run")), None)
    if mutation is None:
        return subprocess.run(["docker", "compose", *args]).returncode
    prefix = ["docker", "compose", *args[:mutation]]
    config = json.loads(subprocess.check_output(prefix + ["config", "--format", "json"],
        stdin=subprocess.DEVNULL, text=True))
    services = {}
    for name, service in config["services"].items():
        condition = service.get("deploy", {}).get("restart_policy", {}).get("condition")
        if service.get("restart", "no") != "no" or condition in ("any", "on-failure"):
            services[name] = {"restart": "on-failure:5", "deploy": {"restart_policy": {
                "condition": "on-failure", "max_attempts": 5}}}
    if not services:
        return subprocess.run(["docker", "compose", *args]).returncode

    explicit_files = any(arg in ("-f", "--file") or arg.startswith(("--file=", "-f=")) or
                         (arg.startswith("-f") and len(arg) > 2) for arg in args[:mutation])
    # Ask Compose to resolve COMPOSE_FILE from its own env loading rules. Do
    # not print this output: other entries can contain deployment credentials.
    environment = subprocess.check_output(prefix + ["config", "--environment"],
        stdin=subprocess.DEVNULL, text=True)
    settings = dict(line.split("=", 1) for line in environment.splitlines()
                    if line.startswith(("COMPOSE_FILE=", "COMPOSE_PATH_SEPARATOR=")))
    separator = settings.get("COMPOSE_PATH_SEPARATOR", os.pathsep)
    files = settings.get("COMPOSE_FILE")
    if not files and not explicit_files:
        for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
            if Path(name).is_file():
                files = name
                break
        if not files:
            raise ValueError("No Compose file found for standby startup")
        for name in ("compose.override.yaml", "compose.override.yml", "docker-compose.override.yml",
                     "docker-compose.override.yaml"):
            if Path(name).is_file():
                files += separator + name
                break
    directory = Path(os.environ.get("HABAK_COMPOSE_GUARD_DIR", "/var/lib/habak-standby/compose-guard"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / (hashlib.sha256(str(Path.cwd()).encode()).hexdigest() + ".json")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as pending:
        json.dump({"services": services}, pending)
        pending_path = Path(pending.name)
    try:
        os.replace(pending_path, target)
    finally:
        pending_path.unlink(missing_ok=True)
    if explicit_files:
        # CLI --file flags take precedence over COMPOSE_FILE. Append the guard
        # to that same CLI list so explicit-file invocations cannot bypass it.
        return subprocess.run(["docker", "compose", *args[:mutation], "--file", str(target),
                               *args[mutation:]]).returncode
    env = dict(os.environ, COMPOSE_FILE=files + separator + str(target), COMPOSE_PATH_SEPARATOR=separator)
    return subprocess.run(["docker", "compose", *args], env=env).returncode


def main():
    os.umask(0o077)
    if sys.argv[1:] == ["--protect-existing"]:
        protect_existing()
        return 0
    return compose(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
