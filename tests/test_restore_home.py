"""Full home restore and recursive local overlay using real files and archives."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "standby/restore_home.py"


class RestoreHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.overlay = self.home / ".overwrite-after-restore"
        self.overlay.mkdir(mode=0o700)
        self.staged = self.root / "staged"
        self.archive = self.root / "home.tar.gz"

    def pack(self, files, links=()):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o600 if name.endswith(".env") else 0o755
                archive.addfile(info, io.BytesIO(content))
            for name, target, kind in links:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = target
                archive.addfile(info)

    def stage(self):
        return subprocess.run([sys.executable, str(SCRIPT), "stage", str(self.archive),
            str(self.staged), "--home-root", str(self.home)], capture_output=True, text=True)

    def apply(self):
        return subprocess.run([sys.executable, str(SCRIPT), "apply", str(self.staged),
            str(self.home)], capture_output=True, text=True)

    def test_all_home_files_and_env_are_overwritten_at_home_root(self):
        (self.home / "midburn-ops-log").mkdir()
        (self.home / "midburn-ops-log/.env").write_text("old settings")
        (self.home / "unrelated-local-file").write_text("retain")
        self.pack({"home/habak/midburn-ops-log/.env": b"BACKUP=complete\n",
            "home/habak/midburn-ops-log/src/nested/code.py": b"source code\n",
            "home/habak/.ssh/config": b"backed-up SSH configuration\n",
            "home/habak/.bashrc": b"backed-up shell configuration\n"})
        staged = self.stage()
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertEqual((self.home / "midburn-ops-log/.env").read_text(), "old settings")
        applied = self.apply()
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.home / "midburn-ops-log/.env").read_bytes(), b"BACKUP=complete\n")
        self.assertTrue((self.home / "midburn-ops-log/src/nested/code.py").is_file())
        self.assertTrue((self.home / ".ssh/config").is_file())
        self.assertTrue((self.home / ".bashrc").is_file())
        self.assertFalse((self.home / "apps").exists())
        self.assertEqual((self.home / "unrelated-local-file").read_text(), "retain")
        self.assertEqual((self.home / "midburn-ops-log/.env").stat().st_mode & 0o777, 0o600)

    def test_recursive_overlay_wins_and_is_not_replaced_by_backup(self):
        target = self.overlay / "midburn-ops-log/.env"
        target.parent.mkdir()
        target.write_bytes(b"LOCAL=override\n")
        target.chmod(0o600)
        nested = self.overlay / "midburn-habak-infra/auth-proxy/authelia/session.yml"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"local session settings\n")
        self.pack({"home/habak/midburn-ops-log/.env": b"PRODUCTION=secret\n",
            "home/habak/.overwrite-after-restore/midburn-ops-log/.env": b"unwanted backup overlay\n",
            "home/habak/.habak1-server-backup-restore.lock": b"must not replace lock"})
        lock = self.home / ".habak1-server-backup-restore.lock"
        lock.write_text("active lock")
        inode = lock.stat().st_ino
        self.assertEqual(self.stage().returncode, 0)
        self.assertEqual((self.staged / "midburn-ops-log/.env").read_bytes(), target.read_bytes())
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual((self.home / "midburn-ops-log/.env").read_bytes(), b"LOCAL=override\n")
        self.assertEqual((self.home / nested.relative_to(self.overlay)).read_bytes(), nested.read_bytes())
        self.assertEqual(target.read_bytes(), b"LOCAL=override\n")
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse((self.staged / ".overwrite-after-restore").exists())

    def test_unsafe_archive_paths_links_and_special_files_are_rejected(self):
        for name, target, kind in [
            ("home/habak/../../escape", None, None),
            ("etc/passwd", None, None),
            ("/home/habak/absolute", None, None),
            ("home/habak/link", "/etc/passwd", tarfile.SYMTYPE),
            ("home/habak/link", "../../escape", tarfile.SYMTYPE),
        ]:
            with self.subTest(name=name, target=target):
                if kind:
                    self.pack({}, [(name, target, kind)])
                else:
                    self.pack({name: b"bad"})
                self.assertNotEqual(self.stage().returncode, 0)
                self.assertFalse((self.root / "escape").exists())

    def test_internal_symbolic_and_hard_links_are_supported(self):
        self.pack({"home/habak/project/data.txt": b"data"}, [
            ("home/habak/project/alias.txt", "data.txt", tarfile.SYMTYPE),
            ("home/habak/project/hard.txt", "home/habak/project/data.txt", tarfile.LNKTYPE)])
        staged = self.stage()
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertEqual(self.apply().returncode, 0)
        self.assertTrue((self.home / "project/alias.txt").is_symlink())
        self.assertEqual((self.home / "project/alias.txt").read_bytes(), b"data")
        self.assertEqual((self.home / "project/hard.txt").read_bytes(), b"data")

    def test_overlay_cannot_write_outside_home(self):
        self.pack({"home/habak/project/file": b"backup"})
        (self.overlay / "escape").symlink_to(self.root / "outside")
        result = self.stage()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "outside").exists())

    def test_existing_destination_symlink_is_replaced_without_following_it(self):
        outside = self.root / "outside"
        outside.write_text("untouched")
        (self.home / "project").symlink_to(outside)
        self.pack({"home/habak/project/file": b"restored"})
        self.assertEqual(self.stage().returncode, 0)
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual(outside.read_text(), "untouched")
        self.assertFalse((self.home / "project").is_symlink())
        self.assertEqual((self.home / "project/file").read_bytes(), b"restored")
