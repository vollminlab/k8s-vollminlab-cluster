# Migrating a PVC to a different StorageClass

`storageClassName` is immutable on a bound PVC, so switching storage backends means
creating a **new** claim and copying the data across. This is the general form of the
procedure used for Prometheus (`longhorn` → `longhorn-r2`) and Portainer
(`local-path` → `longhorn`).

## Why the obvious approach fails

Deleting the PVC and letting the controller recreate it does **not** work when an
operator or Deployment is still running: the controller recreates the pod, which
re-mounts the claim before `pvc-protection` releases it, and the old PVC survives.
The workload must be scaled to zero first — and for a Helm-managed app, Flux must be
suspended so it does not immediately scale it back.

## Procedure

Use a **new claim name** and point the app at it. That keeps the old data intact until
you have verified the migration, and makes rollback a one-line revert.

```bash
NS=portainer
OLD=portainer          # existing claim
NEW=portainer-data     # new claim, declared in git on the target StorageClass
HR=portainer           # HelmRelease name
```

**1. Suspend Flux so it cannot roll the app onto the empty new volume.**

```bash
flux suspend helmrelease $HR -n $NS
```

Suspend **before merging** the PR. The claim itself is applied by the Flux
*Kustomization* (it is a plain manifest), so it is created even while the HelmRelease
is suspended — which is exactly what you want: new volume present, app still on the old one.

**2. Merge the PR and confirm the new claim is Bound.**

```bash
kubectl get pvc -n $NS $NEW    # must reach Bound before copying
```

**3. Scale the app to zero.** An RWO volume cannot be mounted by the copy job while the
app holds it.

```bash
kubectl scale deployment/$HR -n $NS --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/name=$HR -n $NS --timeout=120s
```

**4. Copy the data with a throwaway pod that mounts both claims.** Do not rely on
`kubectl cp` — many app images are distroless and have neither `tar` nor a shell
(Portainer's has neither).

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: pvc-migrate
  namespace: portainer
spec:
  ttlSecondsAfterFinished: 600
  template:
    metadata:
      labels: {app: pvc-migrate, env: production, category: apps}
    spec:
      restartPolicy: Never
      containers:
        - name: copy
          image: alpine:3.23.4
          # -a preserves ownership/permissions/timestamps; Portainer runs as
          # root (runAsUser 0) and cares about the mode on its BoltDB.
          command: ["sh", "-c", "set -e; du -sh /old; cp -a /old/. /new/; sync; du -sh /new; ls -la /new"]
          volumeMounts:
            - {name: old, mountPath: /old}
            - {name: new, mountPath: /new}
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 500m, memory: 256Mi}
      volumes:
        - {name: old, persistentVolumeClaim: {claimName: portainer}}
        - {name: new, persistentVolumeClaim: {claimName: portainer-data}}
```

**Compare the two `du -sh` lines in the job log before continuing.** They should match.

**5. Resume Flux.** The app comes back on the new claim.

```bash
flux resume helmrelease $HR -n $NS
kubectl rollout status deployment/$HR -n $NS
```

**6. Verify the app in its own terms** — not just that the pod is Running. For
Portainer: log in and confirm the Kubernetes environment, teams and registries are
still there.

**7. Only then, delete the old claim.**

```bash
kubectl delete pvc -n $NS $OLD
```

Leave it for a few days if the volume is small. On `local-path` the PV has
`reclaimPolicy: Delete`, so deleting the claim destroys the data irreversibly.

## Gotchas

- **Node-pinned source volumes.** A `local-path` PV carries a *required* nodeAffinity, so
  the copy job is forced onto that node. If the target StorageClass cannot schedule a
  replica there, the job stays `Pending` — check Longhorn's per-node schedulable space first.
- **RWO means one mounter.** The app must be at zero replicas; a `Recreate` strategy alone
  is not enough.
- **Size for the new backend.** 10Gi on `local-path` is free; on Longhorn it is 10Gi × the
  replica count. Size for the data and expand online later.
- **Check `kubelet_volume_stats_*` afterwards.** `local-path`/hostPath volumes publish no
  volume metrics, so a claim that was invisible to `KubePersistentVolumeFillingUp` should
  start reporting once it is on a CSI driver. That is the confirmation the swap really took.
