"""Check a deployed fixed-path stack using a disposable test account.

Run inside the test VM:
  python3 tests/vm_smoke.py --origin https://192.168.50.218:8543 \
    --login-file /home/habak/test-login.json \
    --ca-file auth-proxy/tls/server.crt
Credentials and session cookies are never printed.
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--login-file', type=Path, required=True)
    parser.add_argument('--ca-file', type=Path, required=True)
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    tls = urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(args.ca_file)))
    anonymous = urllib.request.build_opener(tls, NoRedirect())
    cookies = http.cookiejar.CookieJar()
    authenticated = urllib.request.build_opener(tls, urllib.request.HTTPCookieProcessor(cookies))

    def request(client, path, data=None, headers=None):
        headers = {'Origin': origin, **(headers or {})}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(origin + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            response = client.open(req, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    for path in ('/ops/', '/talkies/', '/ai/'):
        status, headers, _ = request(anonymous, path, headers={'Accept': 'text/html', 'Remote-User': 'spoofed-admin', 'Remote-Groups': 'midburn-ops-admin'})
        assert status == 302 and '/auth/' in headers['Location'], (path, status)
    for path in ('/ops-other', '/talkies-other', '/ai-other', '/auth-other', '/api/session'):
        status, headers, _ = request(anonymous, path)
        assert status == 302 and urllib.parse.urljoin(origin + '/', headers['Location']) == origin + '/ops/', path
    status, headers, _ = request(anonymous, '/?from=smoke')
    assert status == 302 and urllib.parse.urljoin(origin + '/', headers['Location']) == origin + '/ops/'
    print('Anonymous access, spoofed identity rejection, exact path boundaries, and fallback redirect passed')

    credentials = json.loads(args.login_file.read_text())
    status, _, _ = request(authenticated, '/auth/api/firstfactor', {**credentials, 'keepMeLoggedIn': False, 'targetURL': origin + '/ops/'})
    assert status == 200 and cookies, f'Login failed: {status}'
    for prefix in ('/ops', '/talkies', '/ai'):
        status, _, html = request(authenticated, prefix + '/')
        assert status == 200, (prefix, status)
        assets = set(re.findall(r'(?:src|href)="(' + prefix + r'/[^"?#]+\.(?:js|css))', html))
        assert assets, (prefix, 'no prefixed assets')
        for asset in assets:
            assert request(authenticated, asset)[0] == 200, asset
        print(f'{prefix}: page and {len(assets)} prefixed assets passed')

    for path in ('/ops/api/session', '/ops/api/events', '/ops/api/dashboard', '/talkies/api/session', '/talkies/api/assets', '/ai/api/me', '/ai/api/library/documents', '/ai/api/health'):
        status, _, body = request(authenticated, path)
        assert status == 200, (path, status)
        value = json.loads(body)
        if path == '/ops/api/session':
            assert value['user']['id'] == credentials['username'] and value['isAdmin']
            assert value['aiUrl'] == '/ai/embed' and value['logoutUrl'] == '/auth/logout'
        if path == '/ai/api/me':
            assert value['username'] == credentials['username'] and value['role'] == 'admin'
        print(f'{path}: API passed')
    status, _, body = request(authenticated, '/ops/api/session', headers={'Remote-User': 'forged-user', 'Remote-Groups': 'midburn-ops-user'})
    assert status == 200 and json.loads(body)['user']['id'] == credentials['username']
    with authenticated.open(origin + '/talkies/api/events', timeout=10) as stream:
        assert stream.status == 200 and 'text/event-stream' in stream.headers['Content-Type']
        assert stream.readline(), 'SSE stream did not send its initial event'
    print('Authenticated identity override and Talkies SSE passed')
    assert request(authenticated, '/ops/deep/link')[0] == 200
    assert request(authenticated, '/talkies/talkies', headers={'Accept': 'text/html'})[0] == 200
    assert request(authenticated, '/ai/embed')[0] == 200
    for path in ('/ops?tab=new', '/talkies?view=test', '/ai?_rsc=smoke'):
        assert request(authenticated, path, headers={'Accept': 'text/html'})[0] == 200, path
    print('Deep links, query strings, and AI embed passed')


if __name__ == '__main__':
    main()
