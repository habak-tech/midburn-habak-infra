"""Validate fixed-path production Compose configuration without starting services."""
import json
import os
import re
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def config(directory, files, example):
    command = ["docker", "compose", "--env-file", example]
    for file in files:
        command.extend(["-f", file])
    command.extend(["config", "--format", "json"])
    env = {**os.environ, "HTTP_PORT": "8181", "HTTPS_PORT": "8543"}
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
                app = config(directory, files, example)["services"][service]
                labels = app["labels"]
                rule = labels[f"traefik.http.routers.{router}.rule"]
                self.assertEqual(rule, f"Path(`{prefix}`) || PathPrefix(`{prefix}/`)")
                middleware = labels[f"traefik.http.routers.{router}.middlewares"]
                self.assertEqual(middleware.split(",")[0], "authelia@docker")
                self.assertEqual("stripprefix" in str(labels), strips)
                self.assertFalse(app.get("ports"))
                if not strips:
                    self.assertIn("/ai/api/health", app["healthcheck"]["test"][-1])

    def test_portal(self):
        portal = config(ROOT / "auth-proxy", ["compose.yaml"], ".env.example")["services"]["authelia"]
        self.assertNotIn("PUBLIC_HOST", portal["environment"])
        self.assertNotIn("PUBLIC_ORIGIN", portal["environment"])
        self.assertEqual(portal["command"], ["authelia", "--config=/config/configuration.yml", "--config=/config/session.yml"])
        self.assertEqual(portal["labels"]["traefik.http.routers.authelia.rule"], "Path(`/auth`) || PathPrefix(`/auth/`)")

    def test_auth_policy_matches_queries_without_sibling_prefixes(self):
        text = (ROOT / "auth-proxy/authelia/configuration.yml").read_text()
        pattern = re.search(r"- '(\^/\(ops\|talkies\|ai\).*)'", text).group(1)
        for path in ("/ops", "/ops?tab=new", "/ai?_rsc=test", "/talkies/api/assets", "/ai/api/chat?conversationId=test"):
            self.assertIsNotNone(re.fullmatch(pattern, path), path)
        for path in ("/ops-other", "/ai-other?x=y", "/talkies-other"):
            self.assertIsNone(re.fullmatch(pattern, path), path)

    def test_proxy_authentication_order_and_redirect_port(self):
        services = config(ROOT / "auth-proxy", ["compose.yaml"], ".env.example")["services"]
        proxy = services["traefik"]
        labels = proxy["labels"]
        self.assertEqual(labels["traefik.http.middlewares.authelia.chain.middlewares"], "clear-identity,authelia-verify")
        for header in ("Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name"):
            self.assertEqual(labels[f"traefik.http.middlewares.clear-identity.headers.customrequestheaders.{header}"], "")
        self.assertEqual(labels["traefik.http.middlewares.authelia-verify.forwardauth.address"], "http://authelia:9091/auth/api/authz/forward-auth")
        self.assertIn("--entrypoints.web.http.redirections.entrypoint.to=:8543", proxy["command"])
        root_pattern = labels["traefik.http.middlewares.home-redirect.redirectregex.regex"]
        for url in ("https://192.0.2.10:8543/", "https://192.0.2.10:8543/?from=bookmark"):
            self.assertIsNotNone(re.fullmatch(root_pattern, url))



if __name__ == "__main__":
    unittest.main()
