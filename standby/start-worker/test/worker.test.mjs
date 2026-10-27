import assert from "node:assert/strict";
import { before, test } from "node:test";

let credentials;
let publicKey;
const origin = "https://starter.example.com";
const password = "fixture-password-longer-than-20-characters";

before(async () => {
  const keys = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  publicKey = keys.publicKey;
  const pem = Buffer.from(await crypto.subtle.exportKey("pkcs8", keys.privateKey)).toString("base64");
  credentials = { type: "service_account", client_email: "starter@midburn-habak.iam.gserviceaccount.com",
    private_key_id: "fixture-key", private_key: `-----BEGIN PRIVATE KEY-----\n${pem}\n-----END PRIVATE KEY-----\n`,
    token_uri: "https://untrusted.example.com/token" };
});

async function fixture({ authAllowed = true, startAllowed = true, googleStatus = 200,
  operationError = false, tokenStatus = 200, networkError = false } = {}) {
  const { createWorker } = await import("../src/index.mjs");
  const calls = [];
  const worker = createWorker(async (url, options) => {
    calls.push({ url: String(url), options });
    if (networkError) throw new Error(`A private upstream failure: ${password}`);
    if (url === "https://oauth2.googleapis.com/token") {
      const params = new URLSearchParams(options.body);
      assert.equal(params.get("grant_type"), "urn:ietf:params:oauth:grant-type:jwt-bearer");
      const [header, payload, signature] = params.get("assertion").split(".");
      const claims = JSON.parse(Buffer.from(payload, "base64url"));
      assert.equal(JSON.parse(Buffer.from(header, "base64url")).alg, "RS256");
      assert.equal(claims.iss, credentials.client_email);
      assert.equal(claims.aud, "https://oauth2.googleapis.com/token");
      assert.equal(claims.scope, "https://www.googleapis.com/auth/compute");
      assert.equal(claims.exp - claims.iat, 3600);
      assert.equal(await crypto.subtle.verify("RSASSA-PKCS1-v1_5", publicKey,
        Buffer.from(signature, "base64url"), new TextEncoder().encode(`${header}.${payload}`)), true);
      return Response.json({ access_token: "fixture-access-token", expires_in: 3600 }, { status: tokenStatus });
    }
    assert.equal(new URL(url).origin, "https://compute.googleapis.com");
    return Response.json(operationError ? { error: { errors: [{ code: "QUOTA_EXCEEDED" }] } } :
      { name: "operation-123", status: "PENDING" }, { status: googleStatus });
  });
  const env = { PAGE_PASSWORD: password, GCP_SERVICE_ACCOUNT: JSON.stringify(credentials),
    GCP_PROJECT: "midburn-habak", GCP_ZONE: "me-west1-c", GCP_INSTANCE: "midburn-habak-cloud-backup",
    AUTH_LIMITER: { limit: async () => ({ success: authAllowed }) },
    START_LIMITER: { limit: async () => ({ success: startAllowed }) } };
  const request = (path = "/", method = "GET", headers = {}) => new Request(origin + path, {
    method, headers: { Authorization: `Basic ${btoa(`habak:${password}`)}`, Origin: origin, ...headers } });
  return { worker, env, calls, request };
}

test("anonymous and incorrect credentials cannot reach Google", async () => {
  const f = await fixture();
  for (const auth of ["", "Basic !!!", `Basic ${btoa("habak:incorrect")}`, `Basic ${btoa(`other:${password}`)}`]) {
    const response = await f.worker.fetch(f.request("/start", "POST", { Authorization: auth }), f.env);
    assert.equal(response.status, 401);
    assert.match(response.headers.get("WWW-Authenticate"), /Basic/);
  }
  assert.equal(f.calls.length, 0);
});

test("authenticated page contains the requested button without secrets or API calls", async () => {
  const f = await fixture();
  const response = await f.worker.fetch(f.request(), f.env);
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /start habak backup server/);
  assert.match(html, /action="\/start" method="post"/);
  assert.ok(!html.includes(password) && !html.includes("PRIVATE KEY"));
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.match(response.headers.get("Content-Security-Policy"), /frame-ancestors 'none'/);
  // Native form POSTs have Origin: null under no-referrer, so the page must
  // retain the origin on same-origin navigation for the CSRF check to work.
  assert.equal(response.headers.get("Referrer-Policy"), "same-origin");
  assert.equal(f.calls.length, 0);
});

test("only a same-origin POST can start the server", async () => {
  const f = await fixture();
  assert.equal((await f.worker.fetch(f.request("/start", "GET"), f.env)).status, 405);
  for (const value of ["", "null", "https://attacker.example.com"]) {
    assert.equal((await f.worker.fetch(f.request("/start", "POST", { Origin: value }), f.env)).status, 403);
  }
  assert.equal((await f.worker.fetch(f.request("/other", "POST"), f.env)).status, 404);
  assert.equal(f.calls.length, 0);
});

test("successful start signs real JWTs and targets only the configured instance", async () => {
  const f = await fixture();
  const response = await f.worker.fetch(f.request("/start?instance=another-vm", "POST"), f.env);
  assert.equal(response.status, 202);
  assert.match(await response.text(), /Start request accepted/);
  assert.equal(f.calls.length, 2);
  const start = f.calls[1];
  assert.equal(new URL(start.url).pathname,
    "/compute/v1/projects/midburn-habak/zones/me-west1-c/instances/midburn-habak-cloud-backup/start");
  assert.match(new URL(start.url).searchParams.get("requestId"), /^[0-9a-f-]{36}$/);
  assert.equal(start.options.method, "POST");
  assert.equal(start.options.headers.Authorization, "Bearer fixture-access-token");
});

test("access tokens are cached without adding instance-read or operation polling", async () => {
  const f = await fixture();
  await f.worker.fetch(f.request("/start", "POST"), f.env);
  await f.worker.fetch(f.request("/start", "POST"), f.env);
  assert.equal(f.calls.filter(call => call.url === "https://oauth2.googleapis.com/token").length, 1);
  assert.equal(f.calls.filter(call => call.options.method === "GET").length, 0);
});

test("failed-password and start rate limits prevent Google calls", async () => {
  const f = await fixture({ authAllowed: false, startAllowed: false });
  assert.equal((await f.worker.fetch(f.request("/", "GET", { Authorization: "wrong" }), f.env)).status, 429);
  assert.equal((await f.worker.fetch(f.request("/start", "POST"), f.env)).status, 429);
  assert.equal(f.calls.length, 0);
});

test("unconfigured secrets fail closed", async () => {
  for (const field of ["PAGE_PASSWORD", "GCP_SERVICE_ACCOUNT", "GCP_PROJECT", "AUTH_LIMITER", "START_LIMITER"]) {
    const f = await fixture();
    delete f.env[field];
    assert.equal((await f.worker.fetch(f.request(), f.env)).status, 503, field);
    assert.equal(f.calls.length, 0);
  }
  const f = await fixture();
  f.env.PAGE_PASSWORD = "short";
  assert.equal((await f.worker.fetch(f.request(), f.env)).status, 503);
});

test("Google rejection and operation errors are never reported as a successful start", async () => {
  for (const args of [{ googleStatus: 403 }, { operationError: true }]) {
    const f = await fixture(args);
    const response = await f.worker.fetch(f.request("/start", "POST"), f.env);
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /Start request accepted/);
  }
});

test("OAuth rejection and network errors never expose credentials or start the VM", async () => {
  for (const args of [{ tokenStatus: 401 }, { networkError: true }]) {
    const f = await fixture(args);
    const result = await f.worker.fetch(f.request("/start", "POST"), f.env);
    assert.equal(result.status, 502);
    const html = await result.text();
    assert.ok(!html.includes(password) && !html.includes("PRIVATE KEY") && !html.includes("fixture-access-token"));
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].url, "https://oauth2.googleapis.com/token");
  }
});

test("HTTP redirects to HTTPS without starting the server or challenging for a password", async () => {
  const f = await fixture();
  const result = await f.worker.fetch(new Request("http://starter.example.com/start", { method: "POST" }), f.env);
  assert.equal(result.status, 308);
  assert.equal(result.headers.get("Location"), "https://starter.example.com/start");
  assert.equal(result.headers.get("WWW-Authenticate"), null);
  assert.equal(f.calls.length, 0);
});
