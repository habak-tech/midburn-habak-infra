"""Validate merged production/path Compose configuration without starting services."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def config(directory, files, example, prefix=""):
    command = ["docker", "compose", "--env-file", example]
    for file in files:
        command.extend(["-f", file])
    command.extend(["config", "--format", "json"])
    env = {**os.environ, "PUBLIC_HOST": "192.0.2.10", "PROD_HOSTNAME": "app.example.org",
           "APP_BASE_PATH": prefix, "HTTP_PORT": "8181", "HTTPS_PORT": "8543"}
    return json.loads(subprocess.check_output(command, cwd=directory, env=env, stderr=subprocess.PIPE))


class PathRoutingTests(unittest.TestCase):
    def test_protected_apps(self):
        cases = [
            ("burn-talkies", ["compose.production.yaml"], ".env.example", "web", "burn-talkies", "/talkies", True),
            ("midburn-ops-log", ["compose.yaml", "compose.prod.yaml"], ".env.example", "app", "midburn-ops-log", "/ops", True),
            ("midburn-habak-ai/Habak AI Agent", ["compose.yaml"], ".env.production.example", "app", "midburn-habak-ai", "/ai", False),
        ]
        for repo, files, example, service, router, prefix, strips in cases:
            with self.subTest(repo=repo):
                directory = WORKSPACE / repo
                normal = config(directory, files, example)["services"][service]
                self.assertEqual(normal["labels"][f"traefik.http.routers.{router}.rule"], "Host(`app.example.org`)")
                app = config(directory, files + ["compose.path.yaml"], example, prefix)["services"][service]
                labels = app["labels"]
                rule = labels[f"traefik.http.routers.{router}.rule"]
                self.assertEqual(rule, f"Host(`192.0.2.10`) && (Path(`{prefix}`) || PathPrefix(`{prefix}/`))")
                self.assertEqual(app["build"]["args"]["APP_BASE_PATH"], prefix)
                middleware = labels[f"traefik.http.routers.{router}.middlewares"]
                self.assertEqual(middleware.split(",")[0], "authelia@docker")
                self.assertEqual("stripprefix" in str(labels), strips)
                self.assertFalse(app.get("ports"))
                if not strips:
                    self.assertIn("/ai/api/health", app["healthcheck"]["test"][-1])

    def test_portal(self):
        portal = config(ROOT / "auth-proxy", ["compose.yaml", "compose.path.yaml"], ".env.example")["services"]["authelia"]
        self.assertEqual(portal["environment"]["COOKIE_DOMAIN"], "192.0.2.10")
        self.assertEqual(portal["environment"]["AUTH_PATH"], "/auth")
        self.assertEqual(portal["environment"]["AUTH_PORTAL_URL"], "https://192.0.2.10:8543/auth")
        self.assertEqual(portal["labels"]["traefik.http.routers.authelia.rule"], "Host(`192.0.2.10`) && (Path(`/auth`) || PathPrefix(`/auth/`))")


if __name__ == "__main__":
    unittest.main()
