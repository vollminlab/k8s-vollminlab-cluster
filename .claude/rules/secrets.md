---
description: SealedSecrets workflow — how to create, seal, and manage secrets in k8s-vollminlab-cluster
---

# Secrets Rules

## Hard rules — enforced by CI (gitleaks) and must never be violated

- **Never commit a plain `kind: Secret`** — only `SealedSecret` (bitnami.com/v1alpha1)
- **Never commit API keys, passwords, tokens, or any credential** in any file — YAML, shell script, markdown, or otherwise
- **Never put a secret value in a ConfigMap** — use `secretKeyRef` to reference it from a SealedSecret
- **Never log or echo a secret value** in a CI step or script

The CI runs gitleaks on every PR as a required check ("Secret Scanning"). If it fires, the PR cannot merge. If you generated a value that was accidentally committed, treat it as compromised and rotate it immediately.

Credentials belong in **1Password** (Homelab vault). Kubernetes secrets belong in **SealedSecrets**.

## 1Password — save before you seal

Every API key, password, or token generated when setting up a new service **must be saved to 1Password before it is sealed or used anywhere else**. This is the source of truth for credential recovery.

**When adding a new service**, save to 1Password at the point the credential is generated (not after):

- App-generated API keys (Readarr, Radarr, SABnzbd, Prowlarr, etc.) → save immediately after first login
- Terraform-managed credentials (robot accounts, OAuth clients, access keys) → save the input values before running `tofu apply`
- Homepage widget API keys → save alongside the app's own entry

**Naming convention for 1Password items** (Homelab vault):

| What | Item name pattern |
|------|------------------|
| App API key | `<AppName> API Key` (e.g. `Readarr API Key`) |
| Service account / robot | `<Service> <Purpose>` (e.g. `Harbor Registry Robot`) |
| OAuth client | `<AppName> OAuth Client` |
| Database password | `<AppName> DB Password` |

Apply the **"Homelab"** tag to every item.

**Retrieving credentials in Claude sessions** — always use `op` CLI, never ask the user to paste values into chat:

```bash
# Sign in first if needed
op signin

# List items to find the right one
op item list --vault Homelab --format json | python3 -c \
  "import json,sys; [print(i['title']) for i in json.load(sys.stdin) if 'readarr' in i['title'].lower()]"

# Get a specific field (use --format json for concealed fields)
op item get "Readarr API Key" --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='api_key'))"
```

## Prefer API keys over local logins

When a service supports API key authentication, use it — never rely on username/password form login unless no API key exists.

**Hard rule: service-to-service connections always use API keys**, not UI credentials. This applies to:

| Connection | Use |
|-----------|-----|
| Prowlarr → Radarr/Sonarr/Readarr | API key (configured via Terraform `prowlarr_application_*`) |
| Readarr/Radarr/Sonarr → SABnzbd | API key (configured via Terraform `download_client_sabnzbd`) |
| Homepage widgets | API key via `homepage-env-vars` SealedSecret |
| Terraform providers | API key (never username/password if both are offered) |
| Monitoring exporters (Exportarr, etc.) | API key |
| Claude sessions accessing apps | API key via `op` CLI — never interactive login |

**When adding a new service:**
1. Generate or locate the API key
2. Save it to 1Password (see above)
3. Wire it into the relevant Terraform module or SealedSecret
4. Never hardcode it, never use the UI login as a substitute

If a service does not expose an API key, use the minimum-privilege local account and store credentials in 1Password.

## Creating a sealed secret

```bash
# 1. Fetch the current sealing certificate
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > pub-cert.pem

# 2. Create and seal (pipe, never write the plain secret to disk)
kubectl create secret generic my-secret -n my-namespace \
  --from-literal=key=value \
  --dry-run=client -o yaml | \
  kubeseal --cert pub-cert.pem --format yaml > my-secret-sealedsecret.yaml

# 3. Delete pub-cert.pem when done (it's public, but no need to keep it)
```

## Sealing key backup

The controller's sealing key is backed up in **1Password** as **"Sealed Secrets Sealing Key"** (Homelab vault). Must be restored before running Flux bootstrap on a rebuilt cluster. Procedure documented in `bootstrap/sealed-secrets/`.

## Referencing secrets in HelmRelease values

Use `valuesFrom` with a `Secret` kind — but the secret itself must be sealed:

```yaml
valuesFrom:
  - kind: Secret
    name: my-app-credentials
    valuesKey: helm-values.yaml
```

Or use `extraEnv` with `secretKeyRef` in the ConfigMap values:

```yaml
extraEnv:
  - name: MY_PASSWORD
    valueFrom:
      secretKeyRef:
        name: my-app-credentials
        key: password
```

## Naming convention

- `metadata.name`: `{app-name}-{purpose}` — never use `-secret` as a suffix (it's redundant; the kind already says Secret)
- Filename: `{metadata.name}-sealedsecret.yaml` — the filename base **must exactly equal** `metadata.name`
- Common purpose suffixes: `-credentials`, `-token`, `-apikey`, `-config`, `-env-vars`

Examples: `harbor-db-credentials`, `renovate-token`, `alertmanager-pushover-config`

Wrong: `harbor-admin-sealedsecret.yaml` containing `metadata.name: harbor-admin-credentials` — the base (`harbor-admin`) doesn't match the name (`harbor-admin-credentials`).
