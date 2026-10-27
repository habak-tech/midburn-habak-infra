"""Run the restore script against real archives and a fake Docker boundary.

All host paths are redirected into a temporary directory; no daemon is used.
"""

import gzip
import fcntl
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "habak1-server-backup.sh"
DUMPS = ("burn-talkies-db", "habak-ai-db", "midburn-ops-log-db")
USERS = ("burn_talkies_bootstrap", "habak", "midburn_ops_log")


def archive(path, files):
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            if name.endswith("/"):
                entry.type = tarfile.DIRTYPE
                entry.mode = 0o750
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))


class RestoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home/habak"
        self.home.mkdir(parents=True)
        self.home.chmod(0o750)
        self.cron_file = self.root / "etc/cron.d/habak-backup"
        self.cron_file.parent.mkdir(parents=True)
        self.cron_file.write_text("* * * * * root backup-script cron\n")
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        (self.uploads / "stale.txt").write_text("old")
        self.backup = self.root / "backup"
        self.backup.mkdir()
        self.bundle = self.root / "habak1-backup.tar.gz"
        self.log = self.root / "docker.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.command("hostname", "#!/bin/sh\necho habak1\n")
        self.command("id", '#!/bin/sh\necho "${TEST_UID:-0}"\n')
        self.command("docker", '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
if "create" in args and "--no-deps" in args:
    print("unknown flag: --no-deps", file=sys.stderr)
    sys.exit(1)
payload = sys.stdin.read() if "run" in args else ""
with open(os.environ["DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "cwd": os.getcwd(), "stdin": payload}) + "\\n")
if args[:2] == ["volume", "inspect"]:
    print(os.environ["UPLOADS"])
if "run" in args and os.environ.get("FAIL_RESTORE"):
    sys.exit(42)
if "pull" in args and os.environ.get("FAIL_PULL"):
    sys.exit(43)
if "config" in args and "--format" in args:
    print(json.dumps({"services":{"app":{"restart":"unless-stopped"},"db":{"restart":"unless-stopped"},"postgres":{"restart":"unless-stopped"},"migrate":{"restart":"no"}}}))
if "config" in args and "--environment" in args:
    print("COMPOSE_FILE=compose.yaml")
''')
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        DOCKER_LOG=str(self.log), UPLOADS=str(self.uploads),
                        HABAK_HOME_RESTORE_SCRIPT=str(SCRIPT.parent / "standby/restore_home.py"))
        source = SCRIPT.read_text().replace("/home/habak", str(self.home))
        source = source.replace("/tmp/habak1-", str(self.root / "habak1-"))
        source = source.replace("/etc/cron.d/habak-backup", str(self.cron_file))
        self.script = self.root / "restore.sh"
        self.script.write_text(source)
        self.make_backup()
        for project in ("burn-talkies", "midburn-habak-ai/Habak AI Agent",
                        "midburn-ops-log", "midburn-habak-infra/auth-proxy"):
            directory = self.home / project
            directory.mkdir(parents=True)
            (directory / "compose.yaml").write_text("existing config\n")
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("existing ssh config\n")

    def command(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def make_backup(self):
        files = {}
        for project in ("burn-talkies", "midburn-habak-ai/Habak AI Agent",
                        "midburn-ops-log", "midburn-habak-infra/auth-proxy"):
            files[f"home/habak/{project}/compose.yaml"] = b"services: {}\n"
        files["home/habak/.ssh/config"] = b"restored ssh config\n"
        archive(self.backup / "home.tar.gz", files)
        # prepare stores the absolute Docker mountpoint with the leading / removed.
        archive(self.backup / "ai-uploads.tar.gz", {
            "var/lib/docker/volumes/habak-ai_uploads/_data/": b"",
            "var/lib/docker/volumes/habak-ai_uploads/_data/document.txt": b"saved upload",
        })
        for dump, user in zip(DUMPS, USERS):
            with gzip.open(self.backup / f"{dump}.sql.gz", "wt") as out:
                out.write(f"CREATE ROLE {user};\nALTER ROLE {user} WITH SUPERUSER;\n"
                          f"-- saved {dump}\n")
        self.pack()

    def pack(self):
        with tarfile.open(self.bundle, "w:gz") as tar:
            tar.add(self.backup, arcname="backup")

    def run_restore(self, answer="RESTORE\n", **env):
        return subprocess.run(["bash", str(self.script), "restore"], input=answer,
                              text=True, capture_output=True, env=dict(self.env, **env))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_restores_uploads_all_dumps_and_home_files(self):
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cron_file.exists())
        self.assertEqual(self.cron_file.with_suffix(".disabled").read_text(),
                         "* * * * * root backup-script cron\n")
        self.assertTrue((self.home / ".ssh/config").exists())
        self.assertEqual((self.home / ".ssh/config").read_text(), "restored ssh config\n")
        self.assertEqual((self.home / "burn-talkies/compose.yaml").read_text(), "services: {}\n")
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o750)
        self.assertEqual((self.uploads / "document.txt").read_text(), "saved upload")
        self.assertFalse((self.uploads / "stale.txt").exists())
        self.assertEqual(self.uploads.stat().st_mode & 0o777, 0o750)
        calls = self.calls()
        restores = [call for call in calls if "run" in call["args"]]
        self.assertEqual(len(restores), 3)
        for call, dump, user in zip(restores, DUMPS, USERS):
            self.assertIn(f"-- saved {dump}", call["stdin"])
            self.assertNotIn(f"CREATE ROLE {user};", call["stdin"])
            self.assertIn(f"ALTER ROLE {user} WITH SUPERUSER;", call["stdin"])
        starts = [i for i, call in enumerate(calls) if "up" in call["args"]]
        self.assertEqual(len(starts), 4)
        self.assertGreater(min(starts), max(i for i, call in enumerate(calls) if "run" in call["args"]))

    def test_decline_and_eof_leave_services_and_data_untouched(self):
        for answer in ("no\n", "", "yes\n"):
            with self.subTest(answer=answer):
                self.run_restore(answer)
                self.assertEqual(self.calls(), [])
                self.assertTrue((self.uploads / "stale.txt").exists())
                self.assertTrue(self.cron_file.exists())

    def test_non_root_is_rejected(self):
        self.assertNotEqual(self.run_restore(TEST_UID="1000").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_corrupt_or_unsafe_home_payload_aborts_before_stopping_services(self):
        for mode in ("missing", "corrupt", "unexpected-path"):
            with self.subTest(mode=mode):
                self.make_backup()
                target = self.backup / "home.tar.gz"
                if mode == "missing":
                    target.unlink()
                elif mode == "corrupt":
                    target.write_bytes(b"not a gzip archive")
                else:
                    archive(target, {"etc/passwd": b"must not be extracted"})
                self.pack()
                result = self.run_restore()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])
                self.assertEqual((self.home / ".ssh/config").read_text(), "existing ssh config\n")

    def test_missing_installed_project_is_created_from_backup(self):
        shutil.rmtree(self.home / "burn-talkies")
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.home / "burn-talkies/compose.yaml").read_text(), "services: {}\n")

    def test_installed_app_root_override(self):
        app_root = self.root / "installed-apps"
        app_root.mkdir()
        for name in ("burn-talkies", "midburn-habak-ai", "midburn-ops-log", "midburn-habak-infra"):
            shutil.move(str(self.home / name), app_root / name)
        result = self.run_restore(HABAK_RESTORE_APP_ROOT=str(app_root))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for call in self.calls():
            if call["args"][0] == "compose" and not any(arg in call["args"] for arg in ("config", "pull")):
                self.assertTrue(call["cwd"].startswith(str(app_root) + "/"))

    def test_overlay_is_applied_before_compose_validation_and_survives_restore(self):
        overlay = self.home / ".overwrite-after-restore/midburn-habak-infra/auth-proxy"
        overlay.mkdir(parents=True)
        (overlay / "compose.yaml").write_text("local compose override\n")
        nested = overlay / "authelia/secrets/SESSION_SECRET"
        nested.parent.mkdir(parents=True)
        nested.write_text("local-secret")
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.home / "midburn-habak-infra/auth-proxy/compose.yaml").read_text(), "local compose override\n")
        self.assertEqual((self.home / "midburn-habak-infra/auth-proxy/authelia/secrets/SESSION_SECRET").read_text(), "local-secret")
        self.assertEqual(nested.read_text(), "local-secret")

    def test_image_pull_failure_preserves_home_files_and_running_deployment(self):
        result = self.run_restore(FAIL_PULL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.home / "burn-talkies/compose.yaml").read_text(), "existing config\n")
        self.assertEqual((self.uploads / "stale.txt").read_text(), "old")
        self.assertFalse(any("down" in call["args"] or "run" in call["args"] for call in self.calls()))

    def test_standby_restore_uses_boot_guard_and_finishes_data_restore_before_starting_apps(self):
        result = self.run_restore(HABAK_COMPOSE_WRAPPER=str(SCRIPT.parent / "standby/compose_guard.py"),
            HABAK_COMPOSE_GUARD_DIR=str(self.root / "guard"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        restores = [i for i, call in enumerate(calls) if "run" in call["args"]]
        starts = [i for i, call in enumerate(calls) if "up" in call["args"]]
        self.assertEqual(len(restores), 3)
        self.assertEqual(len(starts), 4)
        self.assertLess(max(restores), min(starts))
        for path in (self.root / "guard").glob("*.json"):
            config = json.loads(path.read_text())
            self.assertTrue(all(service["restart"] == "on-failure:5" for service in config["services"].values()))

    def test_failed_standby_data_restore_never_starts_apps_or_proxy(self):
        result = self.run_restore(HABAK_COMPOSE_WRAPPER=str(SCRIPT.parent / "standby/compose_guard.py"),
            HABAK_COMPOSE_GUARD_DIR=str(self.root / "guard"), FAIL_RESTORE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in call["args"] for call in self.calls()))

    def test_restore_on_explicit_recovery_host(self):
        self.command("hostname", "#!/bin/sh\necho ori-laptop\n")
        result = self.run_restore(HABAK_RESTORE_HOST="ori-laptop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.uploads / "document.txt").read_text(), "saved upload")

    def test_recovery_host_override_does_not_allow_backups_or_wrong_host(self):
        self.command("hostname", "#!/bin/sh\necho ori-laptop\n")
        self.assertNotEqual(self.run_restore().returncode, 0)
        self.assertNotEqual(self.run_restore(HABAK_RESTORE_HOST="different-host").returncode, 0)
        result = subprocess.run(["bash", str(self.script), "prepare"],
                                capture_output=True, env=dict(self.env, HABAK_RESTORE_HOST="ori-laptop"))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.cron_file.exists())
        self.assertEqual(self.calls(), [])

    def test_missing_backup_is_rejected_before_stopping_services(self):
        self.bundle.unlink()
        self.assertNotEqual(self.run_restore().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_explicit_backup_file(self):
        backup_file = self.root / "downloaded-backup.tar.gz"
        self.bundle.rename(backup_file)
        result = self.run_restore(HABAK_BACKUP_FILE=str(backup_file))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.uploads / "document.txt").read_text(), "saved upload")

    def test_missing_or_corrupt_payload_is_rejected_before_stopping_services(self):
        for mode in ("missing", "corrupt"):
            with self.subTest(mode=mode):
                self.make_backup()
                target = self.backup / "habak-ai-db.sql.gz"
                if mode == "missing":
                    target.unlink()
                else:
                    target.write_bytes(b"broken gzip")
                self.pack()
                self.assertNotEqual(self.run_restore().returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_failed_database_restore_does_not_start_apps(self):
        self.assertNotEqual(self.run_restore(FAIL_RESTORE="1").returncode, 0)
        self.assertFalse(any("up" in call["args"] for call in self.calls()))
        self.assertTrue(self.bundle.exists())

    def start_restore(self):
        process = subprocess.Popen(["bash", str(self.script), "restore"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(self.stop_process, process)
        process.stdin.write("RESTORE\n")
        process.stdin.flush()
        return process

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def wait_for_cron_disable(self, process):
        deadline = time.monotonic() + 5
        while self.cron_file.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.cron_file.exists())

    def test_active_backup_is_allowed_to_finish_before_restore(self):
        with (self.home / ".habak1-server-backup-cron.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            process = self.start_restore()
            self.wait_for_cron_disable(process)
            time.sleep(0.1)
            self.assertIsNone(process.poll())
            self.assertEqual(self.calls(), [])
            fcntl.flock(lock, fcntl.LOCK_UN)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertTrue((self.home / ".ssh/config").exists())

    def test_lock_timeout_uses_one_shared_budget_and_leaves_cron_disabled(self):
        # Shorten only the test copy's deadline; production allows 600 seconds.
        self.script.write_text(self.script.read_text().replace(
            "lock_deadline=$((SECONDS + 600))", "lock_deadline=$((SECONDS + 3))"))
        with (self.home / ".habak1-server-backup-cron.lock").open("w") as cron_lock, \
                (self.home / ".habak1-server-backup-prepare.lock").open("w") as prepare_lock:
            fcntl.flock(cron_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(prepare_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            process = self.start_restore()
            self.wait_for_cron_disable(process)
            time.sleep(2)
            self.assertIsNone(process.poll())
            fcntl.flock(cron_lock, fcntl.LOCK_UN)
            stdout, stderr = process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertIn("Timed out", stderr)
        self.assertFalse(self.cron_file.exists())
        self.assertEqual(self.calls(), [])

    def test_restore_can_be_retried_with_cron_already_disabled(self):
        self.cron_file.rename(self.cron_file.with_suffix(".disabled"))
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cron_file.exists())

    def test_existing_stacks_are_stopped_before_database_restore(self):
        for project in ("burn-talkies", "midburn-habak-ai/Habak AI Agent",
                        "midburn-ops-log", "midburn-habak-infra/auth-proxy"):
            (self.home / project).mkdir(parents=True, exist_ok=True)
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        stops = [i for i, call in enumerate(calls) if "down" in call["args"]]
        self.assertEqual(len(stops), 4)
        self.assertLess(max(stops), next(i for i, call in enumerate(calls) if "run" in call["args"]))

    def test_wrong_archive_paths_are_rejected_before_stopping_services(self):
        for payload, path in (("ai-uploads.tar.gz", "var/lib/docker/volumes/other/_data/file"),):
            with self.subTest(payload=payload):
                self.make_backup()
                archive(self.backup / payload, {path: b"unexpected"})
                self.pack()
                self.assertNotEqual(self.run_restore().returncode, 0)
                self.assertEqual(self.calls(), [])

    @unittest.skipUnless(os.environ.get("TEST_RESTORE_DOCKER") == "1",
                         "set TEST_RESTORE_DOCKER=1 for disposable PostgreSQL integration")
    def test_real_postgres_dump_round_trip_and_network_authentication(self):
        docker = shutil.which("docker")
        prefix = "habak-restore-test-" + uuid.uuid4().hex[:12]
        self.env.update(REAL_DOCKER=docker, TEST_VOLUME_PREFIX=prefix)
        images = ("postgres:17", "postgres:17-alpine", "postgres:17-alpine")
        seed = '''set -euo pipefail
initdb -U "$1" --auth-local=trust >/dev/null
pg_ctl -D "$PGDATA" -w start >/dev/null
trap 'pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null' EXIT
psql -X -U "$1" -d postgres -v ON_ERROR_STOP=1 >/dev/null <<SQL
ALTER ROLE $1 PASSWORD 'fixture-password';
CREATE ROLE fixture_reader LOGIN PASSWORD 'reader-password';
CREATE DATABASE fixture;
\\connect fixture
CREATE TABLE saved_data (value text);
INSERT INTO saved_data VALUES ('restored row');
INSERT INTO saved_data VALUES ('CREATE ROLE $1;');
GRANT SELECT ON saved_data TO fixture_reader;
SQL
pg_dumpall -U "$1"
'''
        for dump, user, image in zip(DUMPS, USERS, images):
            result = subprocess.run([docker, "run", "--rm", "--network", "none",
                                     "--user", "postgres", "--entrypoint", "bash", image,
                                     "-c", seed, "seed", user], capture_output=True, check=True)
            (self.backup / f"{dump}.sql.gz").write_bytes(gzip.compress(result.stdout))
            volume = f"{prefix}-{user}"
            self.addCleanup(subprocess.run, [docker, "volume", "rm", "-f", volume],
                            capture_output=True)
        self.pack()
        stub = (self.bin / "docker").read_text()
        stub += '''
if "run" in args:
    import subprocess
    user = args[-1]
    image = "postgres:17" if user == "burn_talkies_bootstrap" else "postgres:17-alpine"
    volume = os.environ["TEST_VOLUME_PREFIX"] + "-" + user
    command = [os.environ["REAL_DOCKER"], "run", "--rm", "-i", "--network", "none",
               "--user", "postgres", "--entrypoint", "bash",
               "-e", "POSTGRES_USER=" + user, "-e", "POSTGRES_PASSWORD=fixture-password", "-v",
               volume + ":/var/lib/postgresql/data", image] + args[args.index("-c"):]
    sys.exit(subprocess.run(command, input=payload, text=True).returncode)
'''
        self.command("docker", stub)
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Start each restored cluster and connect to its container IP, checking
        # the persisted pg_hba rule, restored password, role grants and row data.
        verify = '''set -euo pipefail
pg_ctl -D "$PGDATA" -w start >/dev/null
trap 'pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null' EXIT
PGPASSWORD=reader-password psql -X -h "$(hostname -i)" -U fixture_reader -d fixture -Atc 'SELECT value FROM saved_data'
'''
        for user, image in zip(USERS, images):
            result = subprocess.run([docker, "run", "--rm", "--user", "postgres",
                                     "--entrypoint", "bash", "-v",
                                     f"{prefix}-{user}:/var/lib/postgresql/data",
                                     image, "-c", verify], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"restored row\nCREATE ROLE {user};")


if __name__ == "__main__":
    unittest.main()
