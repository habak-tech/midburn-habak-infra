# Fixed-path routing validation

Validated on 2026-10-15 against the new `habak-test` VM on `ori-lp`.
The existing host VMs and production deployment were not modified.

## Deployment acceptance

- All application and custom database Docker images built successfully inside
  the VM. The three database migrations exited successfully, and all database
  and application containers report healthy. Traefik and Authelia are running.
- `python3 tests/test_path_routing.py`: 4 tests passed for path-only routers,
  authentication order, identity-header sanitation, exact path boundaries,
  query-string authorization, root redirects, and the published HTTPS port.
- `tests/vm_smoke.py`: passed real login, unauthenticated/spoofed identity
  rejection, authenticated identity override, pages and 18 JS/CSS assets,
  database APIs, AI health, Talkies SSE, deep links, queries, and embed routing.
  This test validates the VM's persistent self-signed certificate explicitly.
- Real Chrome on `ori-lp`: login/logout/re-login verified as `admin`; Ops, Talkies inventory,
  AI, and AI document pages loaded. Ops event creation returned 201.
- Real AI ingestion: the labeled VM test text document reached `ready` with one
  embedded chunk. The Ops AI iframe produced streamed live Anthropic answers;
  a follow-up returned the expected `HABAK-VM-READY` code from the document.
  The first saved deployment credentials had an invalid Voyage key; using
  credentials from the existing backup resolved the failure. The failed
  ingestion record was removed before the successful retry.

The browser checks found and fixed Authelia denying root-path query strings
such as `/ai?_rsc=...`. The root redirect also preserves query strings.

## Source validation

- Ops: 704 tests passed across an isolated Node container run (700) and the
  workspace Docker harness (4). Frontend/server builds, typecheck, and lint
  passed, with existing warnings. Browser E2E suite was not run separately.
- Talkies: production build, lint, 12 targeted regression tests, and 30 HTTP
  adapter integration tests passed. Full unit suite: 407 passed, 3 skipped,
  1 failure in unchanged `server/http/json.test.ts` (expected
  `application/json`, received `application/json;charset=utf-8`). Local E2E
  could not reach its database through the external Docker daemon; temporary
  resources were cleaned up. VM browser and SSE checks passed.
- AI: 66 tests passed, isolated production build passed, and isolated runtime
  checks verified auth, admin redirects, embed, and prefixed assets. Standalone
  TypeScript checks still report existing mock-test `MockModuleOptions.exports`
  typing errors; the production build passed.
- The combined local `path_runtime_smoke.py` passed Ops/Talkies but could not
  start AI because that checkout has an active development build, not a
  production `.next`. AI was checked using its isolated build and the VM.
- `git diff --check` passed in all four repositories.

Fresh VM databases contain only test data. See [the VM runbook](../habak-vm.md)
for access, credentials locations, and verification commands.

## LAN exposure

The full deployed smoke test also passed from the workspace container through
`https://192.168.50.218:8543`. Host socket forwarders are enabled at boot on
ports 8181 and 8543. HTTP redirects preserve the LAN address and HTTPS port.
A request to `/auth/` with an unrelated Host header returned 200, confirming
application selection does not depend on Host. Login cookies and redirects
use the configured canonical LAN origin.
