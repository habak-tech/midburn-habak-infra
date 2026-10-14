# Path routing validation

Validated locally on 2026-10-03 across the infrastructure repository and the
three requested sibling applications. Production services were not changed.

- Production builds passed with `/talkies`, `/ops`, and `/ai` respectively.
- `python3 tests/test_path_routing.py` passed both tests, covering all four
  merged Compose configurations, unchanged domain routers, prefix boundaries,
  authentication middleware order, build arguments, and AI health routing.
- `python3 tests/path_runtime_smoke.py` passed all three applications. It fetched
  prefixed pages and their JS/CSS assets, checked Vite deep links, and verified
  AI root authentication, admin/API authorization, and redirects preserving a
  public IP, port, HTTPS scheme, and prefix.
- Authelia 4.39 `validate-config` accepted both the original domain mode and
  shared-IP `/auth` mode using dummy secrets and no container network access.
- Talkies: `bun run test` passed 396 tests, with 3 existing skips; lint and build
  passed. The prefix tests also exercise JSON API and EventSource URLs.
- AI: `npm test` passed all 64 tests; the Next.js production build, including
  its type-validation step, passed. A separate direct `tsc --noEmit` invocation
  reported existing `MockModuleOptions.exports` errors in
  `lib/chat-workspace.test.ts`, `lib/conversations-api.test.ts`, and
  `lib/library-documents-api.test.ts` with the installed type definitions.
- Ops: typecheck and build passed; lint returned 0 errors and 8 existing
  Fast Refresh warnings. The full suite against the disposable test database
  passed 691 tests and failed these two existing tests:
  - `src/utils/eventAi.test.ts`: `buildEventAiUrl > resolves a root-relative URL
    and states when an open event has no updates` expects a status string that
    the current event context omits.
  - `src/components/events/EventCard.test.tsx`: `EventCard classification >
    opens the configured AI iframe after update and close actions` expects an
    event ID string that the current event context omits.
  Both failures reproduced on pristine HEAD snapshots under `/tmp` with the
  same installed dependencies. Their production files and assertions were
  unchanged by this task.
- `git diff --check` passed in all four repositories.

The test database and all validation app processes were stopped and removed.
No provider API calls were made. Target-host login, real API/SSE traffic, and
iframe browser acceptance remain deployment smoke checks; the local runtime
script uses supplied proxy identity headers and no live application database.
