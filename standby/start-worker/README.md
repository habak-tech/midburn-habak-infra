# Start the Habak backup server from Cloudflare

This independent Cloudflare Worker displays a password prompt and a page with
one **start habak backup server** button. It starts only:

```text
Project:  midburn-habak
Zone:     me-west1-c
Instance: midburn-habak-cloud-backup
```

The Worker remains reachable while the VM and its tunnel are stopped. Use its
`workers.dev` URL or a separate hostname such as `start-habak.uumpa.ai`.
Keep `habakbak.uumpa.ai` pointing to the existing application tunnel.

The dedicated Google service account receives a custom role containing only
`compute.instances.start`, granted on this VM. It cannot read VM status, stop
instances, change metadata, restore backups, or read the backup bucket. The
Worker sends the REST start request directly and reports acceptance without
polling. This matches Google's documented
[start API permission](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances/start)
and [instance IAM binding](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/add-iam-policy-binding).

Starting the VM does not change its restore setting: the default boot preserves
existing data. A restore happens only if previously armed using
`sudo habak-standby --restore-next-boot on` on the VM.

## 1. Create the Google service account and role

Run in your own authenticated administrator terminal with GCloud installed,
or Google Cloud Shell. The VM's storage-only service account cannot provision
these IAM resources. All commands explicitly target `midburn-habak`.

```sh
gcloud auth login
gcloud services enable compute.googleapis.com iam.googleapis.com \
  --project=midburn-habak

gcloud iam service-accounts create habak-backup-starter \
  --project=midburn-habak \
  --display-name="Cloudflare Habak backup starter"

gcloud iam roles create habakBackupVmStarter \
  --project=midburn-habak \
  --title="Start Habak backup VM" \
  --description="Start the backup VM; no read, stop, or administration access" \
  --permissions=compute.instances.start \
  --stage=GA

gcloud compute instances add-iam-policy-binding midburn-habak-cloud-backup \
  --project=midburn-habak \
  --zone=me-west1-c \
  --member=serviceAccount:habak-backup-starter@midburn-habak.iam.gserviceaccount.com \
  --role=projects/midburn-habak/roles/habakBackupVmStarter
```

Do not grant this role at project level or add other roles to the new service
account. No `Service Account User`, `Token Creator`, `Compute Admin`, or Viewer
grant is needed. The new account is independent of the account attached to
the backup VM. If a create command reports that a resource already exists,
inspect its configuration before reusing it; do not create duplicate keys.

Verify the role and the VM-level policy using your administrator identity:

```sh
gcloud iam roles describe habakBackupVmStarter --project=midburn-habak
gcloud compute instances get-iam-policy midburn-habak-cloud-backup \
  --project=midburn-habak --zone=me-west1-c
```

The role's `includedPermissions` must contain exactly `compute.instances.start`.
The VM policy must bind the role to the new service account.

## 2. Generate a service account key outside the repository

In the same terminal:

```sh
umask 077
HABAK_WORKER_KEY_DIR="$HOME/.config/habak-backup-starter"
mkdir -p "$HABAK_WORKER_KEY_DIR"

gcloud iam service-accounts keys create "$HABAK_WORKER_KEY_DIR/gcp-key.json" \
  --project=midburn-habak \
  --iam-account=habak-backup-starter@midburn-habak.iam.gserviceaccount.com
```

This JSON file is a private credential. Upload the entire file as the Worker
secret below; never put it in `wrangler.jsonc`, source control, a browser page,
or the VM's application configuration. If organization policy disallows
service account keys, these instructions cannot finish as written; use an
administrator-approved authentication design instead of weakening the policy.
See Google's [key creation documentation](https://docs.cloud.google.com/iam/docs/keys-create-delete).

## 3. Deploy the Worker and upload its secrets

Use Node.js 24 LTS, npm, and a Cloudflare account with Workers enabled. From
this repository's root, in the same terminal as step 2:

```sh
cd standby/start-worker
npm ci
npm test
npm run build
npx wrangler login
npx wrangler deploy

# Interactive prompt: enter a unique password of at least 20 characters.
npx wrangler secret put PAGE_PASSWORD

# Upload the complete JSON file without printing it.
npx wrangler secret put GCP_SERVICE_ACCOUNT < "$HABAK_WORKER_KEY_DIR/gcp-key.json"
```

Choose the intended Cloudflare account if Wrangler prompts. Record the HTTPS
`workers.dev` URL printed by deployment. On first use, Cloudflare may ask you
to choose your account's `workers.dev` subdomain. The first deployment returns
503 until both secrets are installed; each `secret put` deploys an updated
Worker version. The login username is always **`habak`** and the password is
the value you set in `PAGE_PASSWORD`. Store it in your password manager.
Cloudflare documents these commands in its
[Worker secrets guide](https://developers.cloudflare.com/workers/configuration/secrets/).

The project, zone, and instance are already configured in `wrangler.jsonc`.
There are no Google credentials in that file. Rate limiter namespaces `842671`
and `842672` should be unused by other Workers in your account; change them to
two other unique positive integers if needed. The bindings allow approximately
10 failed logins per IP per minute and one authenticated start per minute per
Cloudflare location. Cloudflare's
[rate limits are local and approximate](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/),
so they are abuse controls rather than a global lock.

After uploading the key successfully, delete the local copy if you do not
need to retain it in an approved secret store:

```sh
rm -- "$HABAK_WORKER_KEY_DIR/gcp-key.json"
```

Deleting this file does not revoke the Google key; the Worker secret still
contains the credential. See rotation and revocation below.

## 4. Optional: give the page its own hostname

The `workers.dev` URL works immediately. For `start-habak.uumpa.ai`, ensure
`uumpa.ai` is active in the same Cloudflare account, then add this top-level
entry to `wrangler.jsonc` (keep the other fields):

```json
"routes": [
  { "pattern": "start-habak.uumpa.ai", "custom_domain": true }
]
```

Run `npx wrangler deploy` again. Cloudflare creates the DNS record and manages
HTTPS for this Worker custom domain. The hostname must be free of conflicting
DNS records or routes. Do not reuse `habakbak.uumpa.ai`, which belongs to the
application tunnel. See the
[custom domain documentation](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).
Both the custom domain and `workers.dev` entry point enforce the same password.

## 5. Verify and use

1. Open the Worker URL in a browser. Confirm that it prompts for a password.
2. Log in as `habak` using the password from step 3. Confirm the start button
   appears. Merely opening the page never starts the VM.
3. When the VM is stopped and you want it online, click **start habak backup
   server**. A 202 page saying **Start request accepted** means Google accepted
   the operation. Allow a few minutes for the VM, applications, and tunnel.
4. Follow **Open Habak backup** to `https://habakbak.uumpa.ai/ops/`. The
   Cloudflare tunnel token must already be configured on the VM as described
   in [the standby guide](../README.md#cloudflare-tunnel). Habak's own login
   is separate from the Worker's password prompt.

An unauthenticated check must return 401 after the secrets are configured:

```sh
HABAK_START_URL='https://YOUR-WORKER-URL'
curl -I "$HABAK_START_URL/"
```

From an administrator terminal, inspect actual VM status if needed:

```sh
gcloud compute instances describe midburn-habak-cloud-backup \
  --project=midburn-habak --zone=me-west1-c --format='value(status)'
```

The Worker deliberately cannot run that read operation. It also cannot stop
the server. Stop it separately through Google Cloud when you want to return
it to standby; stopping a running server interrupts its applications.

## Troubleshooting

- **503:** Check that both secrets are set, the password is at least 20
  characters, and both rate bindings and all three target variables exist.
- **401:** Use username `habak` and the exact password. Browsers cache Basic
  credentials; a private window is useful when testing a changed password.
- **403 on start:** Submit using the page's form. Only a POST with this exact
  HTTPS origin is accepted; cross-site submissions and missing Origin fail.
- **429:** Wait at least a minute before trying again.
- **502:** Google authentication, the start request, or its returned operation
  failed. The request might have reached Google before a network timeout;
  inspect VM status before retrying. Check the full JSON secret, that its
  Google key remains enabled, that the Compute API is enabled, and that the
  role and VM-level binding match step 1. Allow time for IAM propagation.
  An already running VM can also reject start. Use Google Cloud's operation
  history and audit logs for details; credentials and upstream responses
  are never included in the page or Worker logs.
- **Accepted but applications unavailable:** Acceptance is asynchronous, not
  a health check. Inspect the VM's operation in Google Cloud, then its boot
  service and tunnel using the standby guide.

Do not grant broader roles to fix a 502. The only start permission is
`compute.instances.start`; application health checks and operation polling are
intentionally outside this Worker's role.

## Rotate or revoke credentials

Change the page password with `npx wrangler secret put PAGE_PASSWORD`.

For Google key rotation, create a new key using step 2, upload it using step
3, then delete the old key after verification. List user-managed key IDs:

```sh
gcloud iam service-accounts keys list --managed-by=user \
  --project=midburn-habak \
  --iam-account=habak-backup-starter@midburn-habak.iam.gserviceaccount.com

gcloud iam service-accounts keys delete OLD_KEY_ID \
  --project=midburn-habak \
  --iam-account=habak-backup-starter@midburn-habak.iam.gserviceaccount.com
```

Deleting a key prevents new token issuance but existing OAuth tokens can live
until expiry (up to one hour). To remove the start authorization, remove the
instance binding as well; allow for IAM propagation:

```sh
gcloud compute instances remove-iam-policy-binding midburn-habak-cloud-backup \
  --project=midburn-habak --zone=me-west1-c \
  --member=serviceAccount:habak-backup-starter@midburn-habak.iam.gserviceaccount.com \
  --role=projects/midburn-habak/roles/habakBackupVmStarter
```

## Local development

`npm test` uses Node's test runner and generated RSA fixture keys. It verifies
real JWT signatures, exact VM targeting, password and CSRF rejection, rate
limits, token reuse, and Google error handling without accessing Google.
`npm run build` performs a Wrangler deployment dry run without publishing.

For local runtime checks, use ignored `.dev.vars` with a test password and a
test service account JSON value, then `npm run dev`. The Worker redirects HTTP
to HTTPS, so use `npm run dev -- --local-protocol https` and accept the local
certificate. Do not use production credentials for a page-only check. Never
click start with a real key unless you intend to start the configured VM.
