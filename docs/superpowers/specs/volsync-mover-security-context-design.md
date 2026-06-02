# Volsync moverSecurityContext Fix

## Problem

Five apps in `mediastack` have `ReplicationSource` objects that have never completed a successful backup. The restic mover container runs as root (UID 0) by default, but the PVC files are owned by the app's UID. This causes `permission denied` errors on read, which cause the restic job to exit non-zero, triggering an endless retry loop.

Affected apps and their UIDs:

| App | UID | Failing files |
|---|---|---|
| radarr | 568 | `asp/` key files |
| sonarr | 568 | `asp/` key files |
| prowlarr | 568 | `asp/` key files |
| readarr | 568 | `asp/` key files |
| filebrowser | 1000 | `database.db` |

## Fix

Add `moverSecurityContext` to each failing `ReplicationSource` under `spec.restic`:

```yaml
restic:
  moverSecurityContext:
    runAsUser: <uid>
    runAsGroup: <uid>
    fsGroup: <uid>
```

This makes the restic mover container run as the same UID as the app, giving it full read access to all PVC contents.

The existing `exclude: - asp/` on the radarr `ReplicationSource` is ineffective (not a valid Volsync field) and will be removed as part of this change.

## Scope

- 5 files: `radarr/sonarr/prowlarr/readarr/filebrowser-config-replicationsource.yaml` in `clusters/vollminlab-cluster/mediastack/volsync/`
- No Flux index changes required (files are already listed)
- No SealedSecrets involved
- Cluster impact: Volsync will trigger new backup jobs on next scheduled run (03:00 UTC); no app downtime
