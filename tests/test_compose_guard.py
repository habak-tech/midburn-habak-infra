"""Boot-controlled restart policies without changing backed-up Compose files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "standby/compose_guard.py"


class ComposeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.original = self.root / "compose.yaml"
        self.original.write_text("services: {}\n")
        self.log = self.root / "calls.jsonl"
        docker = self.bin / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json,os,sys,pathlib
args=sys.argv[1:]
record={"args":args,"compose_file":os.environ.get("COMPOSE_FILE","")}
if "up" in args or "run" in args:
    files=record["compose_file"].split(":")
    explicit=[args[index+1] for index,arg in enumerate(args[:-1]) if arg in ("-f","--file")]
    if explicit: files=explicit
    record["override"]=json.loads(pathlib.Path(files[-1]).read_text()) if files[-1] else None
    if "run" in args: record["stdin"]=sys.stdin.read()
with open(os.environ["DOCKER_LOG"],"a") as log: log.write(json.dumps(record)+"\\n")
if args[:2]==["ps","--all"]: print("old-web old-migrate unrelated temporary")
elif args[0]=="inspect":
    print(json.dumps([
      {"Id":"old-web","Config":{"Labels":{"com.docker.compose.project":"burn-talkies"}},"HostConfig":{"RestartPolicy":{"Name":"unless-stopped"}}},
      {"Id":"old-migrate","Config":{"Labels":{"com.docker.compose.project":"burn-talkies"}},"HostConfig":{"RestartPolicy":{"Name":"no"}}},
      {"Id":"unrelated","Config":{"Labels":{"com.docker.compose.project":"another-app"}},"HostConfig":{"RestartPolicy":{"Name":"always"}}},
      {"Id":"temporary","Config":{"Labels":{"com.docker.compose.project":"habak-ai"}},"HostConfig":{"AutoRemove":True,"RestartPolicy":{"Name":"always"}}}
    ]))
elif args[0]=="update" and os.environ.get("FAIL_UPDATE"): sys.exit(41)
elif "config" in args:
    if "--environment" in args: print("COMPOSE_FILE=compose.yaml:compose.prod.yaml")
    else: print(json.dumps({"services":{
      "app":{"restart":"unless-stopped"},"db":{"restart":"always"},
      "migrate":{"restart":"no"},"worker":{"deploy":{"restart_policy":{"condition":"any"}}}
    }}))
''')
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
            DOCKER_LOG=str(self.log), HABAK_COMPOSE_GUARD_DIR=str(self.root / "guard"))

    def run_guard(self, *args, **env):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
            env=dict(self.env, **env), input="SQL input\n", text=True, capture_output=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_persistent_services_are_guarded_before_creation_and_compose_is_unchanged(self):
        before = self.original.read_bytes()
        result = self.run_guard("--profile", "cloudflare", "up", "-d", "--no-build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.calls()[-1]
        self.assertEqual(call["args"], ["compose", "--profile", "cloudflare", "up", "-d", "--no-build"])
        self.assertTrue(call["compose_file"].startswith("compose.yaml:compose.prod.yaml:"))
        services = call["override"]["services"]
        self.assertEqual(set(services), {"app", "db", "worker"})
        for service in services.values():
            self.assertEqual(service["restart"], "on-failure:5")
            self.assertEqual(service["deploy"]["restart_policy"]["condition"], "on-failure")
        self.assertEqual(self.original.read_bytes(), before)

    def test_database_restore_stdin_reaches_compose_run_unchanged(self):
        result = self.run_guard("run", "--rm", "db")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["stdin"], "SQL input\n")

    def test_explicit_compose_files_cannot_bypass_restart_policy(self):
        result = self.run_guard("--file", "compose.yaml", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.calls()[-1]
        self.assertEqual(call["args"][:3], ["compose", "--file", "compose.yaml"])
        self.assertEqual(call["args"][3], "--file")
        self.assertEqual(call["override"]["services"]["app"]["restart"], "on-failure:5")

    def test_reads_and_pulls_do_not_create_or_start_containers(self):
        result = self.run_guard("pull", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["args"] for call in self.calls()], [["compose", "pull", "--quiet"]])

    def test_existing_habak_containers_are_updated_without_touching_other_projects(self):
        result = self.run_guard("--protect-existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = [call["args"] for call in self.calls() if call["args"][0] == "update"]
        self.assertEqual(updates, [["update", "--restart=on-failure:5", "old-web"]])

    def test_failure_to_protect_existing_containers_is_reported(self):
        self.assertNotEqual(self.run_guard("--protect-existing", FAIL_UPDATE="1").returncode, 0)
