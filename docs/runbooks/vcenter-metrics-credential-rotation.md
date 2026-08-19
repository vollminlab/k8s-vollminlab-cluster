# Rotating the vCenter metrics credential

`vmware-exporter` authenticates to vCenter as **`prometheus-exporter@vsphere.local`**. That is an
SSO local account, and the vsphere.local **Local Password Policy** expires passwords after
`PasswordLifetimeDays` (90 as of 2026-08-18). When it lapses, the exporter's scrape fails and
`TargetDown` fires.

## Why this is a manual rotation

- **The expiry cannot be queried.** `govc sso.user.ls -json` returns only `Disabled`, `Locked`,
  `Details` and group membership — there is no password-last-set or expiry field anywhere in the
  SSO API. Expiry is only knowable as *rotation date + policy*.
- **There is no per-user "never expires".** `govc sso.lpp.update -PasswordLifetimeDays 0` sets it
  for **every** vsphere.local account including `administrator@`, which is a security-posture change
  we deliberately rejected. A per-user override needs `dir-cli` on the VCSA appliance shell.
- **Automating the rotation would need vCenter credentials inside the cluster** — the cluster whose
  nodes are VMs on that vCenter. That is the same layering violation that rules out managing vSphere
  from the in-cluster tofu-controller.

So the `vcenter-credential-age` CronJob only *warns*; a human rotates.

## The warning

`monitoring/vcenter-credential-age` runs weekly and is pure date arithmetic — **it holds no
credentials and can reach neither vCenter nor 1Password**. When fewer than `WARN_DAYS` (14) remain it
**exits non-zero**, so the existing `KubeJobFailed` alert fires. That is deliberate: it reuses
alerting we already have rather than adding an exporter, a Pushgateway or a new rule.

Because it derives expiry from `ROTATED_AT`, forgetting to bump that makes the alert fire **early,
never late**.

## Rotation procedure

Confirm which account you are touching first — there are three similar 1Password items:

| 1P item | account |
|---|---|
| `vCenter Admin SSO` | `administrator@vsphere.local` |
| **`vCenter Metrics`** | **`prometheus-exporter@vsphere.local`** — this one |
| `vCenter local user SSO` | `vollmin@vsphere.local` |

1. **Generate** a password meeting the policy: 8-20 chars, at least one upper, lower, digit and
   special, no more than 3 identical adjacent, and not one of the last 5. Restrict specials to
   `-_.!@#%` so nothing downstream needs escaping. Check the live policy with `govc sso.lpp.info`.

2. **Set it in vCenter** as administrator. This is the *only* vSphere write required:

   ```bash
   govc sso.user.update -p '<new>' prometheus-exporter
   ```

   Pass **only** `-p`. `-R` (role), `-C` (cert) and `-A` (ActAsUser) would change other attributes.
   Verify afterwards that `govc sso.user.ls -json` still shows `Disabled:false Locked:false`.

3. **Update 1Password first**, before the cluster — it is the source of truth:
   item `vCenter Metrics`, field `password`.

4. **Force the ExternalSecret to re-read it.** This step is the one that bites:

   ```bash
   kubectl annotate externalsecret -n monitoring vmware-exporter-credentials \
     force-sync="$(date +%s)" --overwrite
   ```

   `vmware-exporter-credentials` has `refreshInterval: 24h`. Without this it keeps serving the old
   password for up to a day, and since the chart renders `vmware-exporter-secret` **from** it, the
   next HelmRelease reconcile silently re-applies the expired password — hours after the fix looked
   complete.

5. **Reconcile and restart:**

   ```bash
   flux reconcile helmrelease vmware-exporter -n monitoring --with-source
   kubectl rollout restart deployment/vmware-exporter -n monitoring
   ```

6. **Bump `ROTATED_AT`** in `monitoring/vcenter-credential-age/app/cronjob.yaml` and merge, or the
   warning keeps firing.

## Verify by evidence, not by pod status

The exporter reports `Service is UP` on its own health endpoint even when vCenter auth is broken, and
logs only a downstream `AttributeError: 'NoneType' object has no attribute 'RetrieveContent'` — never
the login failure. So check the data:

```bash
# target is scraping
promtool query instant http://localhost:9090 'up{job="vmware-exporter"}'          # want 1
# and returning real inventory, not just answering
promtool query instant http://localhost:9090 'count(vmware_host_power_state)'     # want 3 (ESXi hosts)
promtool query instant http://localhost:9090 'count(vmware_datastore_capacity_size)'  # want 6
```

Also confirm the log shows `Finished collecting metrics from vcenter.vollminlab.com` with no
`RetrieveContent` lines.

## Tests

`sh check_test.sh` in the app directory — pure shell, no cluster. Must pass under `sh`, `dash` and
`busybox ash`, since the image is alpine.
