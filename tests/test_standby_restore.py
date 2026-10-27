"""Exercise the boot command with real archives and a fake GCS boundary."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "standby/restore_latest.py"
PREFIX = "gs://midburn-habak1-server-backups/hourly/"


class StandbyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.source = self.root / "source.tar.gz"
        home_bytes = io.BytesIO()
        with tarfile.open(fileobj=home_bytes, mode="w:gz") as home:
            files = {
                "burn-talkies/compose.yaml": b"services: {}\n",
                "burn-talkies/compose.production.yaml": b"services: {}\n",
                "midburn-ops-log/compose.yaml": b"services: {}\n",
                "midburn-ops-log/compose.prod.yaml": b"services: {}\n",
                "midburn-habak-ai/Habak AI Agent/compose.yaml": b"services: {}\n",
                "midburn-habak-infra/auth-proxy/compose.yaml": b"services: {}\n",
                "burn-talkies/.env": b"BURN_TALKIES_IMAGE=ghcr.io/example/talkies:latest\nPOSTGRES_PASSWORD=production-secret\n",
            }
            for name, content in files.items():
                entry = tarfile.TarInfo("home/habak/" + name)
                entry.size = len(content)
                home.addfile(entry, io.BytesIO(content))
        with tarfile.open(self.source, "w:gz") as archive:
            entry = tarfile.TarInfo("backup/home.tar.gz")
            entry.size = len(home_bytes.getvalue())
            archive.addfile(entry, io.BytesIO(home_bytes.getvalue()))
        self.listing = self.root / "listing"
        self.listing.write_text(PREFIX + "2026/10/18/23.tar.gz\n" +
                                PREFIX + "2026/10/19/00.tar.gz\n" +
                                PREFIX + "2026/10/19/01.tar.gz.partial\n" +
                                PREFIX + "2026/99/99/99.tar.gz\n")
        gcloud = self.bin / "gcloud"
        gcloud.write_text('''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
if args == ["storage", "ls", os.environ["PREFIX"] + "**"]:
    print(pathlib.Path(os.environ["LISTING"]).read_text())
elif args[:2] == ["storage", "cp"] and len(args) == 4:
    pathlib.Path(os.environ["SELECTED"]).write_text(args[2])
    if os.environ.get("FAIL_DOWNLOAD"):
        pathlib.Path(args[3]).write_bytes(b"partial")
        sys.exit(42)
    shutil.copyfile(os.environ["SOURCE"], args[3])
else:
    sys.exit("Unexpected gcloud command: " + repr(args))
''')
        gcloud.chmod(0o755)
        self.docker_log = self.root / "docker.jsonl"
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["DOCKER_LOG"], "a") as log:
    log.write(json.dumps({"cwd": os.getcwd(), "args": sys.argv[1:]}) + "\\n")
if "config" in sys.argv:
    if "--environment" in sys.argv: print("COMPOSE_FILE=compose.yaml")
    else: print(json.dumps({"services": {"cloudflared": {"restart":"unless-stopped", "environment": {"TUNNEL_TOKEN": os.environ.get("TEST_TUNNEL_TOKEN", "")}}}}))
if sys.argv[1:2] == ["ps"] and os.environ.get("TEST_LEGACY"):
    print("legacy-web")
if sys.argv[1:2] == ["inspect"]:
    print(json.dumps([{"Id":"legacy-web", "Config":{"Labels":{"com.docker.compose.project":"burn-talkies"}},"HostConfig":{"RestartPolicy":{"Name":"unless-stopped"}}}]))
if sys.argv[1:2] == ["update"] and os.environ.get("FAIL_GUARD"):
    sys.exit(46)
if "up" in sys.argv and sys.argv[-1] == "cloudflared" and os.environ.get("FAIL_TUNNEL"):
    sys.exit(44)
if "pull" in sys.argv and os.environ.get("FAIL_PULL"):
    sys.exit(45)
''')
        docker.chmod(0o755)
        self.restore = self.root / "restore.sh"
        self.restore.write_text('''set -euo pipefail
[ "$1" = restore ]
read -r answer
[ "$answer" = RESTORE ]
[ "$COMPOSE_PROFILES" = "" ]
[ "$HABAK_RESTORE_HOST" = "$(hostname)" ]
if [ "${FAIL_RESTORE:-}" = 1 ]; then exit 43; fi
cp "$HABAK_BACKUP_FILE" "$RESTORED"
''')
        self.backup = self.root / "habak1-backup.tar.gz"
        self.backup.write_bytes(b"previous backup")
        self.restored = self.root / "restored"
        self.state = self.root / "state"
        self.state.mkdir()
        self.boot_id = self.root / "boot-id"
        self.boot_id.write_text("boot-before")
        self.script = self.root / "standby.py"
        self.script.write_text(SCRIPT.read_text().replace("/proc/sys/kernel/random/boot_id", str(self.boot_id)))
        (self.root / "compose_guard.py").write_text((SCRIPT.parent / "compose_guard.py").read_text())
        self.app_root = self.root / "apps"
        for project in ("burn-talkies", "midburn-ops-log", "midburn-habak-ai/Habak AI Agent", "midburn-habak-infra/auth-proxy"):
            (self.app_root / project).mkdir(parents=True)
            (self.app_root / project / "compose.yaml").write_text("services: {}\n")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        PREFIX=PREFIX, LISTING=str(self.listing), SOURCE=str(self.source),
                        SELECTED=str(self.root / "selected"), RESTORED=str(self.restored),
                        HABAK_BACKUP_FILE=str(self.backup), DOCKER_LOG=str(self.docker_log),
                        HABAK_COMPOSE_GUARD_DIR=str(self.root / "guard"))
        # Existing restore cases represent an explicitly armed next boot.
        (self.state / "restore-next-boot.json").write_text(json.dumps({"armed_from_boot_id": "boot-before"}))
        self.boot_id.write_text("boot-current")

    def run_restore(self, extra=(), **env):
        return subprocess.run([sys.executable, str(self.script),
                               "--backup-file", str(self.backup),
                               "--state-dir", str(self.state),
                               "--restore-script", str(self.restore),
                               "--app-root", str(self.app_root), *extra],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def test_selects_newest_valid_hour_and_records_success_after_restore(self):
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "selected").read_text(), PREFIX + "2026/10/19/00.tar.gz")
        self.assertEqual(self.restored.read_bytes(), self.source.read_bytes())
        status = json.loads((self.state / "last-success.json").read_text())
        self.assertEqual(status["backup_uri"], PREFIX + "2026/10/19/00.tar.gz")
        self.assertIn("boot_id", status)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_failed_or_invalid_download_does_not_restore_or_replace_previous_backup(self):
        for mode in ("failed", "invalid"):
            with self.subTest(mode=mode):
                if mode == "invalid": self.source.write_bytes(b"not a backup")
                result = self.run_restore(FAIL_DOWNLOAD="1" if mode == "failed" else "")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.backup.read_bytes(), b"previous backup")
                self.assertFalse(self.restored.exists())
                self.assertFalse((self.state / "last-success.json").exists())

    def test_no_backup_aborts_without_restoring(self):
        self.listing.write_text(PREFIX + "2026/10/19/\n")
        self.assertNotEqual(self.run_restore().returncode, 0)
        self.assertEqual(self.backup.read_bytes(), b"previous backup")
        self.assertFalse(self.restored.exists())

    def test_failed_restore_does_not_record_success(self):
        self.assertNotEqual(self.run_restore(FAIL_RESTORE="1").returncode, 0)
        self.assertFalse((self.state / "last-success.json").exists())


    def test_restore_request_is_consumed_and_subsequent_start_preserves_data(self):
        self.assertEqual(self.run_restore().returncode, 0)
        self.restored.unlink()
        self.listing.unlink()
        self.assertEqual(self.run_restore().returncode, 0)
        self.assertFalse(self.restored.exists())
        self.assertFalse((self.state / "restore-next-boot.json").exists())

    def test_default_boot_starts_installed_stacks_without_contacting_gcs(self):
        (self.state / "restore-next-boot.json").unlink()
        self.listing.unlink()
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.backup.read_bytes(), b"previous backup")
        self.assertFalse(self.restored.exists())
        self.assertEqual(json.loads((self.state / "last-boot.json").read_text())["mode"], "existing")
        calls = [json.loads(line) for line in self.docker_log.read_text().splitlines()]
        starts = [call for call in calls if "up" in call["args"]]
        self.assertTrue(all("--no-build" in call["args"] for call in starts))
        self.assertEqual([call["cwd"] for call in starts], [str(self.app_root / p) for p in (
            "burn-talkies", "midburn-habak-ai/Habak AI Agent", "midburn-ops-log", "midburn-habak-infra/auth-proxy")])

    def test_arming_applies_to_next_boot_and_can_be_cancelled(self):
        result = self.run_restore(extra=("--restore-next-boot", "on"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enabled", self.run_restore(extra=("--restore-next-boot", "status")).stdout)
        self.assertEqual(self.run_restore().returncode, 0)
        self.assertFalse(self.restored.exists())
        self.boot_id.write_text("boot-next")
        self.assertEqual(self.run_restore().returncode, 0)
        self.assertTrue(self.restored.exists())
        self.assertIn("disabled", self.run_restore(extra=("--restore-next-boot", "status")).stdout)
        self.assertEqual(self.run_restore(extra=("--restore-next-boot", "on")).returncode, 0)
        self.assertEqual(self.run_restore(extra=("--restore-next-boot", "off")).returncode, 0)
        self.assertIn("disabled", self.run_restore(extra=("--restore-next-boot", "status")).stdout)

    def test_arming_protects_legacy_containers_before_accepting_restore_request(self):
        (self.state / "restore-next-boot.json").unlink()
        result = self.run_restore(extra=("--restore-next-boot", "on"), TEST_LEGACY="1", FAIL_GUARD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / "restore-next-boot.json").exists())
        self.assertFalse(self.restored.exists())
        result = self.run_restore(extra=("--restore-next-boot", "on"), TEST_LEGACY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state / "restore-next-boot.json").exists())

    def test_failure_retries_only_during_the_requested_boot(self):
        self.assertNotEqual(self.run_restore(FAIL_RESTORE="1").returncode, 0)
        self.assertFalse((self.state / "restore-next-boot.json").exists())
        self.assertEqual(self.run_restore().returncode, 0)
        self.assertTrue(self.restored.exists())
        self.assertEqual(self.run_restore(extra=("--restore-next-boot", "on")).returncode, 0)
        self.boot_id.write_text("boot-second")
        self.assertNotEqual(self.run_restore(FAIL_RESTORE="1").returncode, 0)
        self.restored.unlink()
        self.boot_id.write_text("boot-third")
        self.listing.unlink()
        self.assertEqual(self.run_restore().returncode, 0)
        self.assertFalse(self.restored.exists())

    def test_tunnel_starts_only_when_a_token_is_configured(self):
        (self.state / "restore-next-boot.json").unlink()
        for token in ("", "fixture-token"):
            self.docker_log.unlink(missing_ok=True)
            result = self.run_restore(TEST_TUNNEL_TOKEN=token)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(self.docker_log.exists(), "Expected installed services to start")
            calls = [json.loads(line)["args"] for line in self.docker_log.read_text().splitlines()]
            tunnel_starts = [call for call in calls if "up" in call and call[-1] == "cloudflared"]
            self.assertEqual(len(tunnel_starts), int(bool(token)))

    def test_tunnel_failure_after_restore_does_not_repeat_restore(self):
        self.assertNotEqual(self.run_restore(TEST_TUNNEL_TOKEN="fixture", FAIL_TUNNEL="1").returncode, 0)
        self.assertTrue(self.restored.exists())
        self.restored.unlink()
        self.listing.unlink()
        self.assertEqual(self.run_restore(TEST_TUNNEL_TOKEN="fixture").returncode, 0)
        self.assertFalse(self.restored.exists())
        self.assertEqual(json.loads((self.state / "last-boot.json").read_text())["mode"], "restored")


if __name__ == "__main__":
    unittest.main()
