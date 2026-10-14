"""Smoke-test sibling production builds after building /talkies, /ops and /ai."""
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

WORKSPACE = Path(__file__).resolve().parents[2]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def request(url, headers=None):
    try:
        response = OPENER.open(urllib.request.Request(url, headers=headers or {}), timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read().decode(errors="replace")


def smoke_test(repo, prefix, command):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        **os.environ,
        "APP_BASE_PATH": prefix,
        "DATABASE_URL": "postgresql://build:build@127.0.0.1:1/build",
        "NODE_ENV": "production",
    }
    with open(f"/tmp/habak-path-smoke-{prefix[1:]}.log", "w") as log:
        process = subprocess.Popen(
            command + ["--port", str(port)], cwd=WORKSPACE / repo,
            env=env, stdout=log, stderr=log, start_new_session=True,
        )
        try:
            origin = f"http://127.0.0.1:{port}"
            public_host = f"192.0.2.10:{port}"
            headers = {
                "Host": public_host,
                "X-Forwarded-Proto": "https",
                "Remote-User": "path-test",
                "Remote-Groups": "midburn-ops-user",
            } if prefix == "/ai" else {}
            # Next.js canonicalizes /ai/ to /ai; Vite serves the trailing slash.
            home = origin + prefix + ("" if prefix == "/ai" else "/")
            for _ in range(100):
                try:
                    status, _, html = request(home, headers)
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None:
                        raise RuntimeError(f"{repo} server exited; see {log.name}")
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"{repo} server did not start; see {log.name}")
            assert status == 200, (repo, status)
            assets = re.findall(r'(?:src|href)="(' + prefix + r'/[^"?#]+\.(?:js|css))', html)
            assert assets, (repo, "no prefixed assets")
            for asset in assets:
                assert request(origin + asset)[0] == 200, asset
            if prefix == "/ai":
                assert request(origin + "/ai")[0] == 401
                assert request(origin + "/ai/habak-logo.png", headers)[0] == 200
                status, response_headers, _ = request(origin + "/ai/admin/documents", headers)
                assert status == 307, status
                assert response_headers["Location"] == f"https://{public_host}/ai/", response_headers["Location"]
                assert request(origin + "/ai/api/documents", headers)[0] == 403
            else:
                assert request(origin + prefix + "/deep/link")[0] == 200
            print(f"{repo}: prefixed page, {len(assets)} JS/CSS assets, deep links / authorization passed")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required")
    for repo, prefix in (("burn-talkies", "/talkies"), ("midburn-ops-log", "/ops")):
        smoke_test(repo, prefix, [node, "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1"])
    smoke_test("midburn-habak-ai/Habak AI Agent", "/ai", [node, "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1"])


if __name__ == "__main__":
    main()
