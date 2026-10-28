# Kubernetes 1.35 Upgrade Plan (Calico 3.31 first)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all 9 nodes from Kubernetes v1.34.8 (upstream EOL 2026-10-27) to v1.35.9, inside every
component's supported range.

**Architecture:** Two prerequisite PRs, then two live changes, each in its own daytime window with
Scott present: Calico 3.29.1 → 3.31.7 first, then the kubeadm hop via the existing
`ansible-playbooks/playbooks/k8s-upgrade.yml`. Nothing here runs unattended or overnight.

**Tech Stack:** kubeadm, Calico tigera-operator (manifest install), Ansible from `ansible01`, Flux.

**Date:** Drafted 2026-10-27. Tracking issue: #1193. Roadmap: Phase 7.1 hop 3.

---

## Why this order

| Fact (measured 2026-10-27) | Consequence |
| --- | --- |
| Calico 3.29.1 is tested on Kubernetes 1.29–1.32 only | Calico moves first. 3.31 is tested on 1.32–1.35 and installs directly from 3.29 |
| Ten CronJobs pin `docker.io/alpine/kubectl:1.33.4` | After the hop that is two minors behind the server, outside kubectl's ±1 skew. A 1.35 client is in skew with both 1.34 and 1.35, so bump first |
| All 9 nodes are on cgroup v2 | 1.35's kubelet hard-fails on v1; nothing to do |
| kubeadm config is v1beta4; kube-proxy is iptables; no deprecated core API with a removal date | Nothing to do |
| Nothing in the cluster takes etcd snapshots (`etcd-defrag` only defragments) | Take one by hand before the hop |

Per-hop sources are on #1193.

## Out of scope

- **Kernel normalization** (roadmap 7.3). It is a separate `os-patch.yml` run. Bundling it with the
  hop puts two changes in one window and makes a failure ambiguous.
- **Hops 4 and 5** (see the appendix). They are gated on releases that do not exist yet.
- **ingress-nginx replacement.** Covered by `docs/superpowers/specs/gateway-api-migration-design.md`.

---

### Task 1: Bump the kubectl image in the ten CronJobs (PR)

**Files (all `image: docker.io/alpine/kubectl:1.33.4` → `1.35.4`):**
- `clusters/vollminlab-cluster/homepage/homepage/app/pihole-watchdog-cronjob.yaml`
- `clusters/vollminlab-cluster/longhorn-system/longhorn-attachment-ticket-reaper/app/cronjob.yaml`
- `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/cronjob.yaml`
- `clusters/vollminlab-cluster/kube-system/etcd-defrag/app/cronjob.yaml`
- `clusters/vollminlab-cluster/kube-system/kubeadm-cert-renew/app/cronjob-k8scp01.yaml`
- `clusters/vollminlab-cluster/kube-system/kubeadm-cert-renew/app/cronjob-k8scp02.yaml`
- `clusters/vollminlab-cluster/kube-system/kubeadm-cert-renew/app/cronjob-k8scp03.yaml`
- `clusters/vollminlab-cluster/kube-system/kubeadm-cert-monitor/app/cronjob.yaml`
- `clusters/vollminlab-cluster/velero/velero-backup-content-guard/app/cronjob.yaml`
- `clusters/vollminlab-cluster/velero/velero-pvb-healer/app/cronjob.yaml`

`1.35.4` is the newest 1.35 tag on Docker Hub (checked 2026-10-27). A client's patch level does not
matter for skew.

- [ ] **Step 1: Branch and edit**

```bash
git checkout main && git pull && git checkout -b chore/kubectl-image-1.35
grep -rl 'docker.io/alpine/kubectl:1.33.4' clusters | xargs sed -i 's#docker.io/alpine/kubectl:1.33.4#docker.io/alpine/kubectl:1.35.4#'
grep -rc 'alpine/kubectl:1.35.4' clusters | grep -v ':0$' | wc -l   # expect 10
grep -r 'alpine/kubectl:1.33' clusters                             # expect no output
```

- [ ] **Step 2: Run the shell tests.** The scripts are unchanged, but this confirms nothing else moved.

```bash
BUSYBOX=$(command -v busybox) scripts/test-shell-scripts.sh   # expect "all 5 tests passed"
```

- [ ] **Step 3: Commit by name, push, open the PR**

```bash
git add $(grep -rl 'docker.io/alpine/kubectl:1.35.4' clusters)
git commit -m "chore: bump in-cluster kubectl to 1.35.4 ahead of the 1.35 hop"
```

- [ ] **Step 4: After merge, check each job, not the CronJob status.** A Ready CronJob proved
  nothing for pvb-healer in #1055.

```bash
for cj in velero/velero-pvb-healer kube-system/etcd-defrag kube-system/kubeadm-cert-monitor; do
  ns=${cj%/*}; name=${cj#*/}
  kubectl create job -n "$ns" --from=cronjob/"$name" "$name-check-$(date +%s)"
done
kubectl get jobs -A --sort-by=.metadata.creationTimestamp | grep -- '-check-'   # COMPLETIONS 1/1
```

Do **not** trigger `kubeadm-cert-renew` by hand: it rotates certificates. Wait for its schedule, then
check its exit code.

Note for later: Renovate never bumped this image. Check why while you're here: a missing
`packageRules` entry, or a datasource mismatch.

---

### Task 2: Record the Calico target in `bootstrap/calico/` (PR, merged before Task 3)

**Files:**
- Modify: `bootstrap/calico/README.md`: version table and Step 1 URL, plus a note on what moved.

`bootstrap/` is not Flux-managed, so merging this changes nothing live. The README's own "Upgrading
Calico" section says the PR is the upgrade record and the apply happens after merge.

- [ ] **Step 1: Edit the version table and install commands**

```markdown
| Tigera Operator | v1.40.15 |
| Calico | v3.31.7 |
```

Replace the Step 1 install command with the 3.30+ form, which applies the CRDs separately:

```bash
kubectl apply --server-side --force-conflicts -f https://raw.githubusercontent.com/projectcalico/calico/v3.31.7/manifests/operator-crds.yaml
kubectl apply --server-side --force-conflicts -f https://raw.githubusercontent.com/projectcalico/calico/v3.31.7/manifests/tigera-operator.yaml
```

- [ ] **Step 2: Note what 3.31 changes, under the "Upgrading Calico" section**

```markdown
3.31 notes for this install (checked 2026-10-27):
- The `calico-apiserver` namespace is retired; the API server runs in `calico-system`.
  The Step 4 label command for `calico-apiserver` no longer applies after 3.31.
- IPIP routes are programmed by Felix, no longer by BIRD.
- Default image registry is quay.io (was docker.io).
- Goldmane and Whisker (3.30+) are NOT installed unless their CRs are applied. We do not apply them.
```

- [ ] **Step 3: Commit, push, open the PR.** Validate nothing else: CI does not read `bootstrap/`.

---

### Task 3: Calico 3.29.1 → 3.31.7 (live, daytime, Scott present)

Disruption: the operator replaces `calico-node` one node at a time (`maxUnavailable: 1`). Pod
networking on a node blips while its `calico-node` restarts. Running workloads keep their IPs.

- [ ] **Step 1: Baseline.** Save the output; Step 5 compares against it.

```bash
kubectl get tigerastatus                                  # apiserver/calico/ippools AVAILABLE=True
kubectl get installation default -o jsonpath='{.status.calicoVersion}{"\n"}'   # v3.29.1
kubectl get nodes                                         # 9 Ready
P="kubectl exec -n monitoring prometheus-kube-prometheus-stack-prometheus-0 -c prometheus -- promtool query instant http://localhost:9090"
$P 'count(up)'; $P 'count(up==0)'                         # 2026-10-27: 138 and empty
$P 'count(up{job="node-exporter"}==1)'                    # 9: one cross-node scrape per node
```

Per-node tunnel routes, read-only over SSH. Load one key per agent: with nine loaded, ssh runs out of
auth attempts. The worker06 key is ed25519 and must be read with `?ssh-format=openssh`.

```bash
for spec in k8scp01:k8scp01_id_rsa k8scp02:k8scp02_id_rsa k8scp03:k8scp03_id_rsa \
  k8sworker01:k8sworker01_id_rsa k8sworker02:k8sworker02_id_rsa k8sworker03:k8sworker03_id_rsa \
  k8sworker04:k8sworker04_id_rsa k8sworker05:k8sworker05_id_rsa k8sworker06:k8sworker06_id_ed25519; do
  n=${spec%%:*}; k=${spec##*:}
  eval "$(ssh-agent -s)" >/dev/null
  op read "op://Homelab/$k/private key?ssh-format=openssh" | ssh-add - >/dev/null 2>&1
  printf '%s ' "$n"; ssh -o BatchMode=yes -o IdentitiesOnly=no vollmin@$n.vollminlab.com 'ip route | grep -c tunl0'
  ssh-agent -k >/dev/null
done
```

- [ ] **Step 2: Fetch and inspect the manifests**

```bash
D=$(mktemp -d)
curl -fsSLo $D/operator-crds.yaml   https://raw.githubusercontent.com/projectcalico/calico/v3.31.7/manifests/operator-crds.yaml
curl -fsSLo $D/tigera-operator.yaml https://raw.githubusercontent.com/projectcalico/calico/v3.31.7/manifests/tigera-operator.yaml
grep -m1 'image: quay.io/tigera/operator' $D/tigera-operator.yaml   # v1.40.15
kubectl diff --server-side --force-conflicts -f $D/tigera-operator.yaml | head -80
```

- [ ] **Step 3: Apply (the documented 3.31 operator-manifest procedure)**

```bash
kubectl apply --server-side --force-conflicts -f $D/operator-crds.yaml
kubectl apply --server-side --force-conflicts -f $D/tigera-operator.yaml
kubectl rollout status -n tigera-operator deploy/tigera-operator --timeout=5m
```

- [ ] **Step 4: Watch the first node roll before the rest**

```bash
kubectl get pods -n calico-system -o wide -w   # watch the first calico-node replacement
kubectl get tigerastatus                       # PROGRESSING=True during the roll
```

When the first new `calico-node` is Ready, check that node's tunnel-route count against the
baseline, and that `count(up{job="node-exporter"}==1)` is still 9.

**Stop rule.** Any of the following means stop:
- that node's route count drops;
- node-exporter targets fall below 9 for more than 2 scrape intervals;
- a node goes NotReady;
- `tigerastatus calico` shows DEGRADED=True for more than 10 minutes.

To stop, freeze the operator so it rolls nothing further:

```bash
kubectl scale -n tigera-operator deploy/tigera-operator --replicas=0
```

Then diagnose. Calico documents no downgrade path; re-applying the v3.29.1 manifests is the fallback
but is **untested**, so stop early rather than late. The DaemonSet only advances when the new pod is
Ready, so a broken first node halts the roll on its own.

- [ ] **Step 5: Verify the end state against the baseline**

```bash
kubectl get installation default -o jsonpath='{.status.calicoVersion}{"\n"}'   # v3.31.7
kubectl get tigerastatus                                  # all AVAILABLE=True, none DEGRADED
kubectl get pods -n calico-system                         # calico-node 9/9, includes calico-apiserver
kubectl get ns calico-apiserver                           # expect NotFound or Terminating
$P 'count(up)'; $P 'count(up==0)'                         # same as baseline
# Route counts per node: rerun the Step 1 loop and diff.
```

Also check that `calico-system` still carries `app`/`env`/`category` labels, and that Kyverno has no
new failures for the namespace:

```bash
kubectl get ns calico-system --show-labels
```

- [ ] **Step 6: Comment the result on #1193.**

---

### Task 4: Pre-hop checks for 1.35 (same day as Task 5, before it)

- [ ] **Step 1: Backups are current**

```bash
kubectl get backups.velero.io -n velero --sort-by=.metadata.creationTimestamp \
  -o custom-columns='N:.metadata.name,P:.status.phase' | tail -3   # last daily-full and daily-b2 Completed
```

- [ ] **Step 2: Longhorn is fully healthy.** The playbook gates on this between nodes, but start clean.

```bash
kubectl get volumes.longhorn.io -A --no-headers -o custom-columns='R:.status.robustness' | sort | uniq -c
# expect only "healthy" and "unknown"; detached volumes report "unknown". 2026-10-27: 41 healthy, 15 unknown
```

- [ ] **Step 3: Take an etcd snapshot on two control-plane nodes.** It stays on the node: the file
  holds every Secret in the cluster, so do not copy it off-host. `/var/lib/etcd` is the etcd pod's
  hostPath, so the file survives the pod.

```bash
CERTS="--cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key"
TS=$(date -u +%Y%m%dT%H%MZ)
kubectl exec -n kube-system etcd-k8scp01 -- etcdctl --endpoints=https://192.168.152.8:2379 $CERTS snapshot save /var/lib/etcd/pre-1.35-$TS.db
kubectl exec -n kube-system etcd-k8scp02 -- etcdctl --endpoints=https://192.168.152.9:2379 $CERTS snapshot save /var/lib/etcd/pre-1.35-$TS.db
kubectl exec -n kube-system etcd-k8scp01 -- etcdutl snapshot status /var/lib/etcd/pre-1.35-$TS.db -w table
```

`etcdutl` 3.6.5 ships in the etcd image (verified 2026-10-27). In etcd 3.6, `etcdctl snapshot status`
is gone and `etcdutl` replaces it.

- [ ] **Step 4: Kyverno's ServiceCIDR fix (PR #630) is still in place**

```bash
kubectl get cm -n kyverno kyverno -o jsonpath='{.data.resourceFilters}' | tr ']' '\n' | grep -iE 'servicecidr|ipaddress'
```

Expect both kinds listed. 1.33 introduced them, and without the filter Kyverno rejects them with
HTTP 400.

---

### Task 5: The hop, v1.34.8 → v1.35.9 (live, daytime, run by Scott on ansible01)

`1.35.9-1.1` is published in `pkgs.k8s.io/core:/stable:/v1.35` (checked 2026-10-27).

- [ ] **Step 1: First control-plane node only, then stop and look**

```bash
# on ansible01, in ansible-playbooks
git pull
ansible-playbook playbooks/k8s-upgrade.yml -e "target_version=1.35.9 target_minor=1.35" \
  --limit k8scp01.vollminlab.com --ask-vault-pass
```

Then, from any workstation:

```bash
kubectl get nodes -o wide                       # k8scp01 v1.35.9 Ready
kubectl get pods -n kube-system -o wide | grep k8scp01   # apiserver/etcd/controller-manager/scheduler Running
kubectl version                                 # server v1.35.9
```

- [ ] **Step 2: Remaining control plane, then workers.** Uses the same command with
  `--limit 'k8scp02.vollminlab.com,k8scp03.vollminlab.com'`, then `--limit workers`. The playbook
  goes one node at a time with the Longhorn gate.

- [ ] **Step 3: Post-hop verification**

```bash
kubectl get nodes                                               # all 9 v1.35.9 Ready
kubectl get tigerastatus                                        # all AVAILABLE
flux get kustomizations -A | grep -v True                       # expect only the header
flux get helmreleases -A | grep -v True                         # expect only the header
P="kubectl exec -n monitoring prometheus-kube-prometheus-stack-prometheus-0 -c prometheus -- promtool query instant http://localhost:9090"
$P 'count(up==0)'                                               # empty
# Kyverno still enforces: must end in 'admission webhook "validate.kyverno.svc-fail" denied the request'.
# A :latest tag, because restrict-latest-tag is unaffected by the label-injecting mutations
# (a missing-labels probe would be mutated into compliance and pass). Verified 2026-10-27.
kubectl create deployment kyverno-probe --image=harbor.vollminlab.com/vollminlab/triage:latest -n triage --dry-run=server
```

Then confirm the next run of `velero-pvb-healer` and `etcd-defrag` exits 0.

- [ ] **Step 4: Remove the etcd snapshots once Step 3 is clean.** Also update the local kubectl on
  devsbx01; it is v1.32, already outside skew.

---

### Task 6: Record the hop (PR)

**Files:**
- Modify: `.kubernetes-version` (`1.34.8` → `1.35.9`). CI reads it to pick its own kubectl.
- Modify: `docs/roadmap.md`, 7.1 table: hop 3 `done` with the date.
- Modify: `docs/cluster-reference.md`, wherever it states the Kubernetes or Calico version.

- [ ] **Step 1: Edit, run the doc check, commit by name, open the PR**

```bash
./scripts/check-doc-currency.sh   # "doc currency checks passed"
```

- [ ] **Step 2: Close out hop 3 on #1193.**

---

## Appendix: what gates hops 4 and 5 (from #1193, 2026-10-27)

| Before | Required | Status on 2026-10-27 |
| --- | --- | --- |
| Hop 4 (1.36) | ingress-nginx replaced (Phase 7.5) | design: `docs/superpowers/specs/gateway-api-migration-design.md` |
| | Kyverno 1.20 | not released |
| | Flux 2.9 | released |
| | Calico 3.32: the operator chart stops shipping CRDs, `allow-tigera` tier renamed, AdminNetworkPolicy support removed (the empty `adminnetworkpolicy` tier exists here) | released |
| Hop 5 (1.37) | containerd 2.2.4 → 2.3+ on all nodes | released |
| | Calico 3.33 (from 3.31 or 3.32 only) | released |
| | Flux 2.10; descheduler 0.37 | released |
| | cert-manager 1.22; a Longhorn release tested on 1.37 | not released |
