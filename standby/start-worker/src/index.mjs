import { timingSafeEqual } from "node:crypto";

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const encoder = new TextEncoder();
const securityHeaders = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  // no-referrer turns native form POST Origins into "null", breaking the
  // same-origin CSRF check. Keep the origin for our form, omit it off-site.
  "Referrer-Policy": "same-origin",
  "Strict-Transport-Security": "max-age=31536000",
};

function response(body, status = 200, extraHeaders = {}) {
  return new Response(body, { status, headers: {
    ...securityHeaders, "Content-Type": "text/html; charset=utf-8", ...extraHeaders,
  } });
}

function page(message = "", accepted = false) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Habak backup server</title><style>
:root{color-scheme:light;font-family:system-ui,-apple-system,sans-serif;color:#16332c;background:#f3f2eb}
*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;padding:24px}
main{width:100%;max-width:520px;background:#fffef9;border:1px solid #d4dbd1;border-radius:16px;padding:clamp(24px,6vw,48px)}
.eyebrow{margin:0 0 24px;font-size:12px;font-weight:700;letter-spacing:.14em;color:#50665e}
h1{font-size:clamp(30px,7vw,38px);line-height:1.13;letter-spacing:-.035em;margin:0 0 18px}
p{line-height:1.6;color:#4b6158;margin:0 0 24px}button{width:100%;padding:16px 12px;border:0;border-radius:8px;background:#164c3a;color:white;font:600 16px system-ui;cursor:pointer}
button:hover{background:#103b2d}button:focus-visible,a:focus-visible{outline:3px solid #bf8113;outline-offset:4px}
.message{padding:16px;background:${accepted ? "#e9f5ee" : "#fff0da"};border-left:3px solid ${accepted ? "#22784d" : "#ac6800"};color:#16332c}
.note{font-size:14px;margin:20px 0 0}footer{margin-top:32px;padding-top:24px;border-top:1px solid #e1e5dd;font-size:14px}a{color:#164c3a;text-underline-offset:4px}
</style></head><body><main><p class="eyebrow">HABAK / STANDBY</p>
<h1>Bring the backup<br>server online.</h1>
<p>Start the Google Cloud backup server, then allow a few minutes for Habak to become available.</p>
${message ? `<p role="status" class="message">${message}</p>` : ""}
<form action="/start" method="post"><button type="submit">start habak backup server</button></form>
<p class="note">Starting the server preserves its data unless a restore was armed for the next boot.</p>
<footer><a href="https://habakbak.uumpa.ai/ops/" target="_blank" rel="noopener noreferrer">Open Habak backup &nearr;</a></footer>
</main></body></html>`;
}

function configured(env) {
  return typeof env.PAGE_PASSWORD === "string" && env.PAGE_PASSWORD.length >= 10 &&
    typeof env.GCP_SERVICE_ACCOUNT === "string" && env.GCP_SERVICE_ACCOUNT.length > 0 &&
    [env.GCP_PROJECT, env.GCP_ZONE, env.GCP_INSTANCE].every(value =>
      typeof value === "string" && /^[a-z][a-z0-9-]*$/.test(value)) &&
    typeof env.AUTH_LIMITER?.limit === "function" && typeof env.START_LIMITER?.limit === "function";
}

async function authenticated(request, password) {
  const header = request.headers.get("Authorization") || "";
  if (header.length > 4096 || !/^Basic [A-Za-z0-9+/]+={0,2}$/i.test(header)) return false;
  try {
    const supplied = Uint8Array.from(atob(header.slice(6)), char => char.charCodeAt(0));
    const [actual, expected] = await Promise.all([
      crypto.subtle.digest("SHA-256", supplied),
      crypto.subtle.digest("SHA-256", encoder.encode(`habak:${password}`)),
    ]);
    return timingSafeEqual(new Uint8Array(actual), new Uint8Array(expected));
  } catch {
    return false;
  }
}

function base64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function encodeJson(value) {
  return base64url(encoder.encode(JSON.stringify(value)));
}

// The injected fetch function keeps Google I/O separate and makes the signed
// requests testable without real credentials or a running cloud VM.
export function createWorker(fetcher = globalThis.fetch.bind(globalThis)) {
  let tokenCache;

  async function accessToken(secret) {
    if (tokenCache?.secret === secret && tokenCache.expiresAt > Date.now() + 60_000) return tokenCache.token;
    const account = JSON.parse(secret);
    if (account.type !== "service_account" || typeof account.client_email !== "string" ||
        typeof account.private_key !== "string" || typeof account.private_key_id !== "string") {
      throw new Error("Invalid service account configuration");
    }
    const pem = account.private_key.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g, "");
    const key = await crypto.subtle.importKey("pkcs8", Uint8Array.from(atob(pem), char => char.charCodeAt(0)),
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
    const now = Math.floor(Date.now() / 1000);
    const unsigned = `${encodeJson({ alg: "RS256", typ: "JWT", kid: account.private_key_id })}.${encodeJson({
      iss: account.client_email, scope: "https://www.googleapis.com/auth/compute",
      aud: TOKEN_URL, iat: now, exp: now + 3600,
    })}`;
    const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(unsigned));
    const result = await fetcher(TOKEN_URL, {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
        assertion: `${unsigned}.${base64url(signature)}` }).toString(),
      signal: AbortSignal.timeout(15_000),
    });
    if (!result.ok) throw new Error("Google authentication rejected");
    const body = await result.json();
    if (typeof body.access_token !== "string" || !body.access_token ||
        !Number.isFinite(body.expires_in) || body.expires_in <= 0) throw new Error("Invalid Google token response");
    tokenCache = { secret, token: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 };
    return tokenCache.token;
  }

  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      if (url.protocol !== "https:") {
        url.protocol = "https:";
        return response(null, 308, { Location: url.toString() });
      }
      if (!configured(env)) return response("Worker setup is incomplete. Configure its secrets, target VM and rate limit bindings.", 503);
      try {
        if (!await authenticated(request, env.PAGE_PASSWORD)) {
          const allowed = await env.AUTH_LIMITER.limit({ key: request.headers.get("CF-Connecting-IP") || "unknown" });
          if (!allowed.success) return response("Too many password attempts. Wait a minute and try again.", 429, { "Retry-After": "60" });
          return response("Enter the Habak starter username and password.", 401,
            { "WWW-Authenticate": 'Basic realm="Habak backup starter", charset="UTF-8"' });
        }
        if (url.pathname === "/" && (request.method === "GET" || request.method === "HEAD")) {
          return response(request.method === "HEAD" ? null : page());
        }
        if (url.pathname !== "/start") return response("Not found", 404);
        if (request.method !== "POST") return response("Use the start button to submit a POST request.", 405, { Allow: "POST" });
        if (request.headers.get("Origin") !== url.origin) return response("Start requests must come from this page.", 403);
        const allowed = await env.START_LIMITER.limit({ key: `${env.GCP_PROJECT}/${env.GCP_ZONE}/${env.GCP_INSTANCE}` });
        if (!allowed.success) return response(page("A start request was made recently. Wait a minute before trying again."), 429, { "Retry-After": "60" });
      } catch {
        return response("The password or request limiter is temporarily unavailable. Try again later.", 503);
      }

      try {
        const token = await accessToken(env.GCP_SERVICE_ACCOUNT);
        const target = `https://compute.googleapis.com/compute/v1/projects/${env.GCP_PROJECT}/zones/${env.GCP_ZONE}/instances/${env.GCP_INSTANCE}/start`;
        const result = await fetcher(`${target}?requestId=${crypto.randomUUID()}`, {
          method: "POST", headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(20_000),
        });
        if (!result.ok) throw new Error("Google start request rejected");
        const operation = await result.json();
        if (!operation.name || operation.error) throw new Error("Google start operation failed");
        return response(page("Start request accepted. Allow a few minutes, then open Habak backup. This confirms the request was accepted; the applications may still be starting.", true), 202);
      } catch {
        // Keep credentials, OAuth responses and upstream error details out of
        // both browser responses and Worker logs.
        return response(page("Google did not confirm the start request. The server may already be running, or the request may still have reached Google. Check its state in Google Cloud before retrying. See the setup guide for IAM and credential checks."), 502);
      }
    },
  };
}

export default createWorker();
