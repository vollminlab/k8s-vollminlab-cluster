---
description: Finish a deployment — add it to Homepage, expose it externally via the Cloudflare tunnel, gate it in Authentik, and update the docs. Use this whenever an app, CronJob, tunnel, or piece of infrastructure has just been deployed and verified, or whenever someone asks to "expose", "share", or "publish" something, or to give a service a dashboard entry. A merged PR and Running pods are NOT a finished rollout — the discoverability and access steps live outside the app's own manifests and are the ones that get forgotten.
user-invocable: true
---

# Post-Deploy Completion

A Flux-managed app can be merged, reconciled, Running and serving — and still be
invisible and unreachable to the people who need it. Everything in this checklist
lives **outside** the app's own `app/` directory, which is exactly why it gets
skipped: the PR looks complete because the app is complete.

Run this after an app is deployed and verified healthy.

## Why each step exists

Work top to bottom. Skip what genuinely doesn't apply, but say which and why —
"not applicable" is a decision worth recording, because an omitted step is
indistinguishable from a forgotten one.

---

## 1. Homepage entry

Without this, the service is reachable only by someone who already knows the URL.

Edit `clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml`. The
service list is **inside** `data["values.yaml"]` as an embedded YAML string, so
indentation is load-bearing and a mistake renders as a silently empty dashboard
section rather than an error.

Pick the group that matches how the service is used, not what it technically is:
`Media Stack`, `Infrastructure`, `Networking`, `Monitoring & Observability`,
`Tools`, `Web Links`, `Personal`.

```yaml
            - Display Name:
                description: One short line, sentence case, no trailing period
                href: https://<host>.vollminlab.com
                icon: <name>.png          # or mdi-* / si-* for icons with no logo
                namespace: <k8s-namespace>
                app: <value of the `app` label>
```

`namespace` + `app` are what make Homepage render **live pod status**. Omit them
and the tile still links correctly but shows nothing about health — which is the
main reason to have a dashboard at all. Only genuinely external links (a vendor
site) should lack them.

Verify all three of these, because each fails silently:

```bash
# 1. the EMBEDDED values.yaml still parses, and your entry is in the group
python3 -c "
import yaml
d=yaml.safe_load(open('clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml'))
v=yaml.safe_load(d['data']['values.yaml'])
for g in v['config']['services']:
    name=list(g.keys())[0]
    print(name, '->', [list(e.keys())[0] for e in g[name]])
"

# 2. Homepage's ServiceAccount can read pods in the new namespace
kubectl auth can-i list pods -n <namespace> \
  --as=system:serviceaccount:homepage:homepage

# 3. after Flux reconciles, the tile actually shows status (not "unknown")
```

If `can-i` says no, Homepage's ClusterRole needs the namespace — but it is
cluster-scoped today, so `no` means something else is wrong and is worth
understanding before moving on.

---

## 2. External access — only if someone outside the VPN needs it

An Ingress plus a Pi-hole record is **LAN/VPN only**. Public reachability needs
two tofu resources plus a Pi-hole line, and the third is the one that silently
breaks LAN clients.

Everything lives in `terraform/cloudflare/`, applied by the `cloudflare-config`
Terraform CR. **That CR is `approvePlan: auto`, so merging applies within ~10
minutes** — fine for these resources, but never merge a provider bump alongside
them (see `docs/runbooks/tofu-provider-bumps.md`).

**a. Route the hostname through the shared tunnel** —
`terraform/cloudflare/tunnels.tf`, in
`cloudflare_zero_trust_tunnel_cloudflared_config.nginx`, **before** the
`http_status:404` catch-all (entries are evaluated in order, so anything after it
is dead):

```hcl
      {
        hostname = "<host>.vollminlab.com"
        service  = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80"
      },
```

**b. Public DNS** — `terraform/cloudflare/dns.tf`:

```hcl
resource "cloudflare_dns_record" "<name>" {
  zone_id = var.cloudflare_zone_id
  name    = "<host>.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.nginx.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}
```

**c. Stop the LAN AAAA leak** — this has recurred for *every* tunnel host added
so far, and it is invisible from outside.

Pi-hole overrides the **A** record for LAN clients so they reach the ingress VIP
directly. It does not override **AAAA**. Once Cloudflare serves an AAAA for the
name, dual-stack LAN clients prefer IPv6 and egress out to Cloudflare and back
in through the tunnel — slower, and it masks tunnel breakage because LAN testing
still "works". The fix makes dnsmasq authoritative for the name, so AAAA returns
NODATA while the A override still applies.

Add `local=/<host>.vollminlab.com/` to pihole1's `misc.dnsmasq_lines`. Note the
per-item `PUT` that works for `dns/hosts` **404s** on this key — it needs a
`PATCH` of the whole array, so build the new array from a backup rather than
retyping it:

```bash
# back up first
curl -sS 'http://192.168.100.2/api/config/misc/dnsmasq_lines' > /tmp/dnsmasq-backup.json

python3 -c "
import json
lines=json.load(open('/tmp/dnsmasq-backup.json'))['config']['misc']['dnsmasq_lines']
new='local=/<host>.vollminlab.com/'
assert new not in lines
json.dump({'config':{'misc':{'dnsmasq_lines':lines+[new]}}}, open('/tmp/patch.json','w'))
print('was', len(lines), '-> now', len(lines)+1)
"
curl -sS -X PATCH -H 'Content-Type: application/json' \
  --data-binary @/tmp/patch.json 'http://192.168.100.2/api/config'
```

**Only ever edit pihole1 (192.168.100.2).** nebula-sync runs `FULL_SYNC` hourly
on the hour and copies the whole config to pihole2, so a pihole2 edit is both
unnecessary and will be overwritten. Expect a ≤1 hour convergence window — until
it passes, pihole2 answers with the old config, and it is your clients'
*secondary* resolver.

Verify A and AAAA on **both** Pi-holes and the keepalived VIP, and confirm the
`PATCH` didn't disturb anything else:

```bash
for r in 192.168.100.2 192.168.100.3 192.168.100.4; do
  echo "--- $r"
  dig +short @$r <host>.vollminlab.com A        # expect the ingress VIP
  dig +noall +comments @$r <host>.vollminlab.com AAAA | grep status
  dig +short @$r github.com A                    # unrelated name still resolves
done
```

---

## 3. Authentik — the default is open, not closed

Two separate things, and the second is the one that surprises people.

**a. The Application must exist**, even for a service using the domain-wide
forward-auth provider with `provider_id=None`. Without it the outpost cannot
match the request and returns 400, which nginx converts to a 500. See
`.claude/rules/authentik-akshell.md`.

**b. Decide explicitly who may reach it.** An Application with **no
PolicyBinding is reachable by every authenticated user.** Measured 2026-09-27:
only 2 of 33 Applications had a binding. Accounts on this Authentik are not all
family — some belong to friends and league opponents — so "behind Authentik" is
not by itself a statement about *who* can get in.

For anything not meant for everyone, a group binding alone is **not enough**. On
an Application with `provider_id=None`, the domain-wide forward-auth provider
admits any logged-in account and never evaluates the binding. Verified
2026-10-10 with a no-group account, which reached two group-bound apps. The app
needs its own `forward_single` provider. Do it in tofu (`terraform/authentik/`),
copying FileBrowser and Foundry:

1. `groups.tf` — the `<Service> Users` group and its members.
2. `providers_proxy.tf` — an `authentik_provider_proxy` with
   `mode = "forward_single"` and `external_host = "https://<host>.vollminlab.com"`.
3. `applications.tf` — `protocol_provider` on the Application, plus an
   `authentik_policy_binding` of the group to it.
4. `outpost.tf` — add the provider to `protocol_providers`.

Two cluster changes go in the same PR:

- On the app's Ingress, set `auth-signin` to
  `https://$http_host/outpost.goauthentik.io/start?rd=https://$http_host$escaped_request_uri`.
- Add the host to `clusters/vollminlab-cluster/authentik/authentik-proxy/app/ingress.yaml`,
  so `/outpost.goauthentik.io` on it reaches the outpost.

If the app reads `X-authentik-groups` itself, a group check in the app is an
alternative. Slate Builder does this.

The tofu CR is `approvePlan: auto`, so merging applies it. Then prove the
restriction with an account that is **not** in the group: log in and confirm
Authentik denies it. A member reaching the app proves nothing.

---

## 4. Shlink short link

Automatic: the `shlink.vollminlab.com/slug: <name>` annotation on the Ingress
makes shlink-ingress-controller create `vollm.in/<slug>`. Confirm it happened
rather than assuming:

```bash
kubectl logs -n shlink -l app=shlink-ingress-controller --tail=50 | grep <slug>
```

`post-deploy-shlink` is a **bootstrap-only** bulk-create script from before the
controller existed; don't use it for a single new app.

---

## 5. Documentation — CI enforces two of these

- **`docs/cluster-reference.md`** — every `clusters/<ns>/<app>/` directory must
  appear, enforced by `scripts/check-doc-currency.sh`. Record the container port,
  auth model, whether it's externally exposed, and anything non-obvious a future
  reader would otherwise have to rediscover.
- **`README.md`** — the `Repository Structure` block must list every namespace
  directory, enforced by `scripts/check-readme-structure.sh`. A **new namespace**
  needs a line here; a new app in an existing namespace does not.
- **`docs/roadmap.md`** — only if this changes what a phase *means*. Most apps,
  and every incident-response CronJob, were never on a plan; retroactively
  roadmapping them invents a plan that never existed. See `.claude/rules/docs.md`.
- **`.claude/rules/networkpolicy.md`** — add a row to the port table for any new
  NetworkPolicy port, since that table is the source of truth over service YAML.

Run both checks locally; they're fast and need no cluster:

```bash
./scripts/check-doc-currency.sh && ./scripts/check-readme-structure.sh
```

---

## 6. Verify the deployment, not the status

CI's `integration-test` job prints `✅ All changed HelmReleases render,
strict-parse, and pass server-side dry-run` — and on a PR with **no HelmRelease**
it finds zero, passes in under a second, and dry-runs nothing. 36 of 89 app dirs
here are raw-manifest, so a Deployment/Service/Ingress/NetworkPolicy PR gets a
green check with **no admission testing at all**.

So do it yourself. A throwaway namespace needs the three standard labels, because
`require-standard-labels` is in Enforce and a bare `kubectl create namespace` is
rejected:

```bash
TESTNS=ci-local-$$
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: $TESTNS
  labels: {app: <app>, env: production, category: <category>}
EOF

kustomize build clusters/vollminlab-cluster/<ns> | python3 -c "
import sys; ns='$TESTNS'
docs=sys.stdin.read().split('\n---\n')
print('\n---\n'.join(d.replace('namespace: <ns>','namespace: '+ns)
                     for d in docs if 'kind: Namespace' not in d))
" | kubectl apply --server-side --field-manager=kustomize-controller --dry-run=server -f -

kubectl delete namespace $TESTNS --wait=false
```

Use `--server-side`, because that is what Flux does and the two disagree in a way
that matters: client-side apply copies the whole object into the
`kubectl.kubernetes.io/last-applied-configuration` annotation, so any ConfigMap
over ~256 KB fails client-side while succeeding under Flux. A client-side-only
failure is not necessarily a real one — and a client-side pass is not proof either.

After it's live, verify the artifact rather than the phase:

```bash
# pods Running AND the ConfigMap the Deployment references is the current one
kubectl get pods,cm -n <ns>
kubectl get deploy -n <ns> <app> -o jsonpath='{.spec.template.spec.volumes[*]}'

# ingress-nginx can actually reach the backend on its CONTAINER port
NGX=$(kubectl get pod -n ingress-nginx -l app.kubernetes.io/component=controller -o name | head -1 | cut -d/ -f2)
kubectl exec -n ingress-nginx "$NGX" -- \
  curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' http://<svc>.<ns>.svc.cluster.local/

# forward-auth returns 302 to Authentik, not 500
curl -sS -o /dev/null -D - https://<host>.vollminlab.com/ | grep -iE '^HTTP|^location'
```

A 302 from the ingress is generated **before** it proxies to the backend, so it
proves auth wiring and nothing about reachability — which is why the in-cluster
curl above is a separate check. That distinction is what the container-port
NetworkPolicy bug hides behind.

---

## 7. Renaming or retiring — what does not clean itself up

`external-dns` runs `policy: upsert-only` (a `sync` misconfiguration wiped all
infrastructure DNS on 2026-04-05 and is forbidden). It will never delete, so a
rename or removal leaves orphans that must be removed by hand:

- **Pi-hole A record** — `DELETE /api/config/dns/hosts/<IP>%20<host>` on pihole1.
  Back up `/api/config/dns/hosts` first and confirm the count drops by exactly one.
- **Pi-hole `local=` line** — `PATCH /api/config` with the line removed.
- **Shlink slug** — the controller creates but never deletes:
  `DELETE /rest/v3/short-urls/<slug>` with `X-Api-Key` from the
  `shlink-credentials` Secret (key `initial-api-key`). Check `visitsCount` first;
  someone may be holding the link.
- **Cloudflare tunnel route and DNS record** — remove both tofu resources.
- **Authentik Application, Group and PolicyBinding** — `ak shell`.
- **Homepage entry** and the `cluster-reference.md` row.

Pass secrets to `curl` through a 0600 config file (`header = "X-Api-Key: ..."`)
or stdin rather than `-H` on the command line, so they stay out of the process
table, and shred the file afterwards.

---

## Report back

State which steps you did, which you skipped and why, and which need someone
else to act — a tofu PR that auto-applies on merge, or a ≤1 hour Pi-hole
convergence window, are both things the requester needs to know rather than
discover.
