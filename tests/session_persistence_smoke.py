"""Exercise real Authelia sessions and Redis durability in a disposable stack.

Run: python3 tests/session_persistence_smoke.py
For a Docker host outside this container, pass --host <reachable-host-IP>.
Uses production images, Redis options, and Authelia configuration with synthetic
users/secrets/origins. Never mounts deployment data or prints cookies/passwords.
"""
import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
THIRTY_DAYS = 2592000


def run(*command):
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    project = 'habak-session-test-' + secrets.token_hex(5)
    production = json.loads(run('docker', 'compose', '--project-directory', str(ROOT / 'auth-proxy'),
                                '--env-file', str(ROOT / 'auth-proxy/.env.example'),
                                'config', '--format', 'json'))
    portal = production['services']['authelia']
    redis = production['services']['session-redis']
    assert not redis.get('ports'), 'Session storage must not be published'
    assert set(redis['networks']) == {'sessions'}
    assert production['networks']['sessions']['internal']
    assert portal['restart'] == redis['restart'] == 'unless-stopped'
    assert portal['depends_on']['session-redis']['condition'] == 'service_healthy'
    portal.pop('labels', None)
    portal['volumes'] = [{'type': 'volume', 'source': 'test-config', 'target': '/config'}]
    portal['ports'] = [{'target': 9091, 'host_ip': args.host, 'published': '0'}]
    portal['environment'] = {
        'AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE': '/config/secrets/JWT_SECRET',
        'AUTHELIA_SESSION_SECRET_FILE': '/config/secrets/SESSION_SECRET',
        'AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE': '/config/secrets/STORAGE_ENCRYPTION_KEY',
    }
    spec = {'services': {'authelia': portal, 'session-redis': redis},
            'networks': {'authproxy': {}, 'sessions': {'internal': True}},
            'volumes': {'test-config': {}, 'authelia-sessions': {}}}
    password = secrets.token_hex(24)
    with tempfile.TemporaryDirectory(prefix=project) as directory:
        directory = Path(directory)
        compose_file = directory / 'compose.json'
        compose_file.write_text(json.dumps(spec))

        def compose(*arguments):
            return run('docker', 'compose', '-p', project, '-f', str(compose_file), *arguments)

        def container(service):
            return compose('ps', '-aq', service)

        config = directory / 'config'
        config.mkdir()
        (config / 'configuration.yml').write_text((ROOT / 'auth-proxy/authelia/configuration.yml').read_text())
        (config / 'session.yml').write_text('session:\n  cookies:\n    - domain: example.test\n'
                                          '      authelia_url: https://example.test/auth\n')
        (config / 'secrets').mkdir()
        for name in ('JWT_SECRET', 'SESSION_SECRET', 'STORAGE_ENCRYPTION_KEY'):
            (config / 'secrets' / name).write_text(secrets.token_hex(32))
        digest = run('docker', 'run', '--rm', portal['image'], 'authelia', 'crypto',
                     'hash', 'generate', 'argon2', '--password', password).split('Digest: ')[-1].strip()
        (config / 'users.yml').write_text('users:\n  session-test:\n    displayname: Session Test\n'
                                         f'    password: {json.dumps(digest)}\n    groups: [midburn-ops-user]\n')
        port = None

        def request(path, cookie=None, data=None, extra=None):
            headers = {'Host': 'example.test', 'X-Forwarded-Proto': 'https',
                       'X-Forwarded-Host': 'example.test', 'Origin': 'https://example.test'}
            if cookie:
                headers['Cookie'] = cookie
            if data is not None:
                headers['Content-Type'] = 'application/json'
            headers.update(extra or {})
            connection = http.client.HTTPConnection(args.host, port, timeout=3)
            try:
                connection.request('POST' if data is not None else 'GET', path,
                                   json.dumps(data) if data is not None else None, headers)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        def ready():
            nonlocal port
            port = int(compose('port', 'authelia', '9091').rsplit(':', 1)[1])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if request('/auth/api/health')[0] == 200:
                        return
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(.2)
            raise AssertionError('Authelia did not become healthy')

        def login(remember):
            status, headers, _ = request('/auth/api/firstfactor', data={
                'username': 'session-test', 'password': password,
                'keepMeLoggedIn': remember, 'targetURL': 'https://example.test/ops/'})
            assert status == 200, ('login', status)
            jar = SimpleCookie()
            jar.load(headers['Set-Cookie'])
            cookie = jar['authelia_session']
            if remember:
                remaining = (parsedate_to_datetime(cookie['expires']) - datetime.now(timezone.utc)).total_seconds()
                assert THIRTY_DAYS - 60 <= remaining <= THIRTY_DAYS + 60, remaining
            return 'authelia_session=' + cookie.value

        def verify(cookie, expected=200):
            for prefix in ('ops', 'talkies', 'ai'):
                status, headers, _ = request('/auth/api/authz/forward-auth', cookie, extra={
                    'X-Forwarded-Uri': '/' + prefix + '/', 'X-Forwarded-Method': 'GET'})
                assert status == expected, (prefix, status, expected)
                if expected == 200:
                    assert headers.get('Remote-User') == 'session-test'

        try:
            compose('create', 'authelia')
            run('docker', 'cp', str(config) + '/.', container('authelia') + ':/config/')
            compose('up', '-d', '--wait', '--wait-timeout', '60')
            ready()
            compose('exec', '-T', 'authelia', 'authelia', 'validate-config',
                    '--config', '/config/configuration.yml', '--config', '/config/session.yml')
            a, b = login(False), login(True)
            assert a != b
            verify(a)
            verify(b)
            # Check the actual session lifetimes stored by Authelia, not YAML text.
            keys = compose('exec', '-T', 'session-redis', 'redis-cli', '--raw', 'KEYS', '*').splitlines()
            assert len(keys) == 2, len(keys)
            for key in keys:
                ttl = int(compose('exec', '-T', 'session-redis', 'redis-cli', 'TTL', key))
                assert THIRTY_DAYS - 60 <= ttl <= THIRTY_DAYS, ttl
            print('PASS: two sessions, 30-day Redis TTLs, and 30-day remembered cookie')
            compose('restart', 'authelia')
            ready()
            verify(a)
            verify(b)
            print('PASS: both sessions survive Authelia restart')
            compose('stop', 'authelia', 'session-redis')
            compose('up', '-d', '--wait', '--wait-timeout', '60', '--force-recreate')
            ready()
            verify(a)
            verify(b)
            print('PASS: both sessions survive full stack stop and container recreation')
            # Kill without graceful Redis shutdown: data must recover from disk.
            compose('kill', '-s', 'SIGKILL', 'authelia', 'session-redis')
            compose('up', '-d', '--wait', '--wait-timeout', '60')
            ready()
            verify(a)
            verify(b)
            print('PASS: both sessions survive abrupt process termination and recovery')
            assert request('/auth/api/logout', a, data={})[0] == 200
            verify(a, 401)
            verify(b)
            compose('restart', 'authelia', 'session-redis')
            ready()
            verify(a, 401)
            verify(b)
            print('PASS: logout remains revoked after restart; other session stays valid')
        finally:
            compose('down', '-v', '--remove-orphans')


if __name__ == '__main__':
    main()
