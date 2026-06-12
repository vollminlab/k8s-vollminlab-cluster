---
description: ESO + 1Password Connect workflow — how to create, reference, and manage secrets in k8s-vollminlab-cluster
---

# Secrets Rules

## How secrets work in this cluster (ESO + 1Password Connect)

Every Kubernetes secret in this cluster is materialized by the **External Secrets Operator (ESO)**
from **1Password** via **1Password Connect**. The migration off SealedSecrets is complete
(controller removed 2026-05-31) — **there are no SealedSecrets in the repo and none may be added.**

The chain:

```
1Password (Homelab vault)
   └─ 1Password Connect  (1password ns: onepassword-connect HelmRelease)
        └─ ClusterSecretStore  (onepassword-cluster-store)
             └─ ExternalSecret  (one per app, in the app's own namespace)
                  └─ Secret      (materialized by ESO, creationPolicy: Owner)
                       └─ consumed by the app via secretKeyRef / valuesFrom
```

- **Source of truth = 1Password.** The repo holds only `ExternalSecret` CRs that *reference*
  vault items by name — never the secret values themselves.
- ESO is deployed in the `external-secrets` namespace; 1Password Connect in the `1password`
  namespace. The `onepassword-cluster-store` `ClusterSecretStore` is cluster-scoped, so any
  namespace's `ExternalSecret` can reference it.

## Hard rules — enforced by CI (gitleaks) and must never be violated

- **Never commit a plain `kind: Secret`** — only `ExternalSecret` (external-secrets.io/v1)
- **Never commit a `SealedSecret`** — the controller is gone; a committed SealedSecret will never reconcile
- **Never commit API keys, passwords, tokens, or any credential** in any file — YAML, shell script, markdown, or otherwise
- **Never put a secret value in a ConfigMap** — reference it from an ESO-materialized Secret via `secretKeyRef`
- **Never log or echo a secret value** in a CI step or script

The CI runs gitleaks on every PR as a required check ("Secret Scanning"). If it fires, the PR cannot merge. If you generated a value that was accidentally committed, treat it as compromised and rotate it immediately.

Credentials belong in **1Password** (Homelab vault). Kubernetes secrets are **materialized from 1Password by ESO** — never created by hand, never sealed, never committed.

## 1Password — save before you wire

Every API key, password, or token generated when setting up a new service **must be saved to 1Password before it is referenced or used anywhere else**. This is the source of truth for credential recovery *and* the live source ESO reads from — if it isn't in 1Password, the ExternalSecret has nothing to sync.

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
| Homepage widgets | API key via the `homepage-env-vars` ExternalSecret |
| Terraform providers | API key (never username/password if both are offered) |
| Monitoring exporters (Exportarr, etc.) | API key |
| Claude sessions accessing apps | API key via `op` CLI — never interactive login |

**When adding a new service:**
1. Generate or locate the API key
2. Save it to 1Password (see above)
3. Wire it into the relevant Terraform module or `ExternalSecret`
4. Never hardcode it, never use the UI login as a substitute

If a service does not expose an API key, use the minimum-privilege local account and store credentials in 1Password.

## Creating an ExternalSecret

1. **Save the credential to the Homelab vault in 1Password first** (see naming convention above).
2. **Write an `ExternalSecret` CR** in the app's directory, referencing the vault item by name. ESO
   materializes a Secret with the same `target.name` in the same namespace.

There are two common shapes.

**`dataFrom.extract`** — pull every field of a vault item into one Secret (use when the app
consumes a bag of env vars, like Homepage):

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: homepage-env-vars
  namespace: homepage
  labels:
    app: homepage
    env: production
    category: apps
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: homepage-env-vars
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: "Homepage Env Vars"      # the 1Password item title
```

**`data` with `remoteRef`** — map specific vault fields to specific Secret keys (use when the
app/HelmRelease expects named keys):

```yaml
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: my-app-credentials
    creationPolicy: Owner
  data:
    - secretKey: password               # key in the resulting k8s Secret
      remoteRef:
        key: "My App DB Password"       # 1Password item title
        property: password              # field label within the item
```

3. **Add the file to the directory's `kustomization.yaml`** (Flux uses explicit lists, not globs).
4. **Verify after Flux reconciles:**

```bash
kubectl get externalsecret <name> -n <namespace>     # STATUS / READY=True
kubectl get secret <target-name> -n <namespace>      # the materialized Secret exists
```

`READY=True` with `SecretSynced` means ESO pulled the value and created the Secret. A `SecretSyncedError`
usually means the item title or field `property` doesn't match 1Password exactly.

## Disaster recovery — the 1Password Connect credentials are the new root secret

The old sealing-key model is gone. The single bootstrap secret that everything else now depends on is
the **1Password Connect** credentials: the `onepassword-connect` Secret in the `1password` namespace,
holding `1password-credentials.json` and `token`. ESO cannot read 1Password without it, and it is
**not** managed by Flux (it would be a chicken-and-egg dependency).

- Both the credentials JSON and the Connect token are stored in **1Password** (Homelab vault).
- On a rebuilt cluster they must be applied **before** Flux bootstrap, so 1Password Connect and ESO
  come up able to sync everything else. Bootstrap order: control plane → Calico CNI → apply the
  `onepassword-connect` Secret → bootstrap Flux (deploys 1Password Connect + ESO, which then
  materialize every other Secret from the vault).

> `bootstrap/sealed-secrets/` is retained only as historical reference for the pre-2026-05-31
> SealedSecrets setup. It is no longer part of the live DR path.

## Referencing secrets in HelmRelease values

The secret is materialized by ESO; HelmReleases consume it exactly as before.

Use `valuesFrom` with a `Secret` kind (the Secret is created by the app's `ExternalSecret`):

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

- `ExternalSecret` `metadata.name` (and `target.name`): `{app-name}-{purpose}` — never use `-secret`
  as a suffix (it's redundant; the kind already says Secret)
- Filename: `{metadata.name}-externalsecret.yaml` — the filename base **must exactly equal** `metadata.name`
- Common purpose suffixes: `-credentials`, `-token`, `-apikey`, `-config`, `-env-vars`

Examples: `harbor-db-credentials`, `renovate-token`, `alertmanager-pushover-config`

Wrong: `harbor-admin-externalsecret.yaml` containing `metadata.name: harbor-admin-credentials` — the base (`harbor-admin`) doesn't match the name (`harbor-admin-credentials`).

## 1Password vault item naming — cluster infrastructure

Vault item names and field labels are cluster infrastructure — an `ExternalSecret` references them by exact string:

- Never rename a vault item referenced by an ExternalSecret without first updating the
  ExternalSecret CR and merging the PR
- Field label names are locked — use: `username`, `password`, `api_key`, `token`,
  `credentials_json`, `access_key_id`, `secret_access_key`, `dockerconfigjson`
- Procedure: update ExternalSecret CR → merge PR → Flux reconciles → verify Ready → rename in 1Password
- Note on vault items: add "Referenced by ExternalSecret — do not rename fields" to item notes
