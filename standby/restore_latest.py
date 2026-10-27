#!/usr/bin/env python3
"""Start the standby, restoring only when explicitly armed for this boot."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import sys


def newest_backup(listing, prefix):
    pattern = re.compile(re.escape(prefix) + r"(\d{4}/\d{2}/\d{2}/\d{2})\.tar\.gz")
    candidates = []
    for line in listing.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            try:
                date = datetime.strptime(match[1], "%Y/%m/%d/%H")
            except ValueError:
                continue
            candidates.append((date, match[0]))
    if not candidates:
        raise ValueError(f"No hourly backup found under {prefix}")
    return max(candidates)[1]


def write_json(path, value):
    pending = path.with_name(path.name + ".new")
    pending.write_text(json.dumps(value, indent=2) + "\n")
    pending.replace(path)


def configure_tunnel(app_root, env):
    proxy = app_root / "midburn-habak-infra/auth-proxy"
    command = ["docker", "compose", "--profile", "cloudflare"]
    config = json.loads(subprocess.check_output(command + ["config", "--format", "json"],
                                               cwd=proxy, env=env, text=True))
    token = config["services"]["cloudflared"]["environment"].get("TUNNEL_TOKEN", "")
    if token and token.strip():
        print("Starting configured Cloudflare tunnel", flush=True)
        subprocess.run([sys.executable, env["HABAK_COMPOSE_WRAPPER"], "--profile", "cloudflare",
                        "up", "-d", "--no-build", "--wait", "--wait-timeout", "90", "cloudflared"],
                       cwd=proxy, env=env, check=True)
    else:
        subprocess.run(command + ["stop", "cloudflared"], cwd=proxy, env=env, check=True)
        print("Cloudflare token is empty; tunnel remains stopped", flush=True)


def restore_backup(args, env, boot_id):
    listing = subprocess.check_output(["gcloud", "storage", "ls", args.prefix + "**"], text=True)
    uri = newest_backup(listing, args.prefix)
    print(f"Downloading {uri}", flush=True)
    with tempfile.NamedTemporaryFile(prefix=".habak-backup-", dir=args.backup_file.parent,
                                     delete=False) as temporary:
        download = Path(temporary.name)
    try:
        subprocess.run(["gcloud", "storage", "cp", uri, str(download)], check=True)
        subprocess.run(["gzip", "-t", str(download)], check=True)
        os.chmod(download, 0o600)
        with download.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        os.replace(download, args.backup_file)
    finally:
        download.unlink(missing_ok=True)
    print(f"Restoring {uri}", flush=True)
    subprocess.run(["bash", str(args.restore_script), "restore"],
                   input="RESTORE\n", text=True, env=env, check=True)
    write_json(args.state_dir / "last-success.json", {
        "backup_uri": uri, "sha256": digest,
        "completed_at": datetime.now(timezone.utc).isoformat(), "boot_id": boot_id})
    print(f"Standby restored successfully from {uri}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="gs://midburn-habak1-server-backups/hourly/")
    parser.add_argument("--backup-file", type=Path, default=Path("/tmp/habak1-backup.tar.gz"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/habak-standby"))
    parser.add_argument("--restore-script", type=Path,
                        default=Path("/usr/local/lib/habak/habak1-server-backup.sh"))
    parser.add_argument("--app-root", type=Path, default=Path("/home/habak"))
    parser.add_argument("--restore-next-boot", choices=("on", "off", "status"),
                        help="Arm, cancel, or inspect a one-boot restore request")
    args = parser.parse_args()
    compose_wrapper = Path(__file__).with_name("compose_guard.py")
    os.umask(0o077)
    os.environ["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "restore.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        request = args.state_dir / "restore-next-boot.json"
        active = args.state_dir / "restore-active.json"
        if args.restore_next_boot:
            if args.restore_next_boot == "on":
                # Protect legacy containers before accepting the reboot request.
                # Docker must not resurrect these while the next boot restores.
                subprocess.run([sys.executable, str(compose_wrapper), "--protect-existing"], check=True)
                write_json(request, {"armed_from_boot_id": boot_id})
            elif args.restore_next_boot == "off":
                request.unlink(missing_ok=True)
            print("Next boot restore: " + ("enabled" if request.exists() else "disabled"))
            return
        env = dict(os.environ, HABAK_RESTORE_HOST=socket.gethostname(),
                   HABAK_RESTORE_APP_ROOT=str(args.app_root), HABAK_BACKUP_FILE=str(args.backup_file),
                   HABAK_COMPOSE_WRAPPER=str(compose_wrapper),
                   COMPOSE_PROFILES="", COMPOSE_PROGRESS="plain", CLOUDSDK_CORE_DISABLE_PROMPTS="1")
        if request.exists() and json.loads(request.read_text())["armed_from_boot_id"] != boot_id:
            write_json(active, {"boot_id": boot_id})
            request.unlink()
        restoring = active.exists() and json.loads(active.read_text())["boot_id"] == boot_id
        if restoring:
            restore_backup(args, env, boot_id)
            # Tunnel/startup failures must never repeat a completed database restore.
            active.unlink()
        else:
            active.unlink(missing_ok=True)
            print("Starting installed services; preserving existing data", flush=True)
            for project in ("burn-talkies", "midburn-habak-ai/Habak AI Agent", "midburn-ops-log",
                            "midburn-habak-infra/auth-proxy"):
                subprocess.run([sys.executable, str(compose_wrapper), "up", "-d", "--no-build", "--remove-orphans", "--wait",
                                "--wait-timeout", "180"], cwd=args.app_root / project, env=env, check=True)
        configure_tunnel(args.app_root, env)
        last_restore = args.state_dir / "last-success.json"
        restored_this_boot = last_restore.exists() and json.loads(last_restore.read_text())["boot_id"] == boot_id
        write_json(args.state_dir / "last-boot.json", {
            "mode": "restored" if restored_this_boot else "existing", "boot_id": boot_id,
            "completed_at": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    main()
