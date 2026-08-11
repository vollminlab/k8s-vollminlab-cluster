# Homepage Dashboard Runbook

## Auto-discovery via ingress annotations

Homepage runs with `kubernetes.mode: cluster` and auto-discovers services from ingress annotations. **Do not add a manual entry in the configmap for any service that already has these annotations on its ingress** — it will create a duplicate that shows as DOWN/NOT FOUND.

Check before adding a manual entry:
```bash
kubectl get ingress <name> -n <namespace> -o jsonpath='{.metadata.annotations}' | grep gethomepage
```

Services currently auto-discovered (as of 2026-04-01):
- **Headlamp** (`flux-system/headlamp-ingress`) → Infrastructure group
- **Longhorn** (`longhorn-system/longhorn-ingress`) → Infrastructure group
- **MinIO** (`minio/minio-ingress`) → Infrastructure group
- **Policy Reporter** (`kyverno/policy-reporter-ui`) → Monitoring & Observability group

## Pod selector gotcha

Homepage translates `app: <value>` to the label selector `app.kubernetes.io/name=<value>`. If a pod uses the plain `app=<value>` label instead (common with Bitnami and some other charts), the status check will show NOT FOUND.

Fix: use `podSelector` to specify the exact label selector instead of `app`:
```yaml
- MyService:
    namespace: my-namespace
    podSelector: "app=my-app"   # exact label, not app.kubernetes.io/name
```

Check what labels a pod actually has before configuring:
```bash
kubectl get pod -n <namespace> --show-labels
```

## Adding a new service via ingress annotations

```yaml
annotations:
  gethomepage.dev/enabled: "true"
  gethomepage.dev/name: "My Service"
  gethomepage.dev/description: "What it does"
  gethomepage.dev/group: "Infrastructure"   # must match an existing group name
  gethomepage.dev/icon: "my-icon.png"
  gethomepage.dev/pod-selector: "app=my-app"
```

## Widget types

**Always verify widget support at https://gethomepage.dev/widgets/services/ before adding a widget block.** Do not guess based on service name — many services do not have homepage widgets.

Notable services with NO homepage widget:
- MinIO (feature request filed, not implemented)
- Shlink

Longhorn is an **info widget** (top bar), not a service widget. Configure it under `config.widgets` and `config.settings.providers.longhorn`, not in `config.services`.

## Credentials

All API keys and passwords live in the **Homepage Env Vars** item in the 1Password Homelab vault. ESO
materializes them into the `homepage-env-vars` Secret via
`clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-externalsecret.yaml`, which pulls
every field of that item with `dataFrom.extract`. Nothing secret is stored in this repo.

To add a new key:

1. Add the field to the **Homepage Env Vars** 1Password item (Homelab vault). Use the env var name as
   the field label, e.g. `HOMEPAGE_VAR_MY_NEW_KEY`.
2. Wait for ESO to re-sync (`refreshInterval: 1h`) or force it immediately:
   ```bash
   kubectl annotate externalsecret homepage-env-vars -n homepage \
     force-sync=$(date +%s) --overwrite
   kubectl get externalsecret homepage-env-vars -n homepage   # READY=True / SecretSynced
   ```
3. Reference the key in the configmap as `"{{HOMEPAGE_VAR_MY_NEW_KEY}}"`, and add a matching env var
   entry in the HelmRelease values that sources it via `secretKeyRef` from `homepage-env-vars`.

Because `dataFrom.extract` pulls the whole item, no ExternalSecret change is needed for a new field —
only the configmap/env wiring. See `.claude/rules/secrets.md` for the full ESO workflow.
