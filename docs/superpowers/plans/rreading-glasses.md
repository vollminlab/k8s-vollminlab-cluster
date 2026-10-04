# rreading-glasses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Created:** 2026-10-04
**Branch:** `feat/rreading-glasses`

**Goal:** Readarr's "Add New" search reliably finds the books Scott is looking for. To get there, we replace the overloaded shared metadata server with a self-hosted rreading-glasses (Hardcover mode) that uses Scott's own Hardcover API quota.

**Architecture:** Readarr runs `ghcr.io/pennydreadful/bookshelf:hardcover-*`. Its saved metadata source is the community server `https://hardcover.bookinfo.pro`, and on 2026-10-04 that server answered 0 of 11 searches: each one hit a 429 or a 100s timeout, from inside the cluster and from devsbx01 alike. That server runs `blampe/rreading-glasses`, so a self-hosted copy speaks the same API and returns the same Hardcover IDs. This keeps the existing library intact: 129 authors and 1,491 books, all keyed by Hardcover IDs. We run it as one Deployment in `mediastack` with a Postgres sidecar on `localhost`; rreading-glasses uses Postgres only as a cache. We then point Readarr at it.

**Tech Stack:** Flux raw manifests (Deployment, Service, PVC), ExternalSecret (ESO + 1Password), Docker for the local proof, Python 3 stdlib for the proof script.

---

## Why Phase 0 is a hard gate

Scott's requirement is that search actually *finds books he is looking for*. Self-hosting fixes the outage, but research found four reasons it might still not meet that bar. Each must be measured before any cluster work starts:

1. **Ranking:** rreading-glasses sorts Hardcover results `ratings_count:desc` *before* `_text_match:desc` and keeps only 15 hits (`hardcover/queries.graphql` L166-177). A short or common-word query can return 15 popular books that are not the one wanted.
2. **Free-tier burst limit of 10:** rreading-glasses #595 reports searches returning empty `[]` with HTTP 200 on the free tier, and a 429 is not retried. #574 (same symptom, reported on our exact bookshelf `v0.4.20.129`) appears fixed only in the unpublished-source image from 2026-09-09.
3. **Silent drops:** a search hit is discarded if its follow-up fetch fails. Failures therefore show up as *fewer results*, never as errors.
4. **bookshelf #134 (open, unfixed through v0.4.21.182):** on author refresh, works with 3+ editions are dropped, so popular books can be missing from an author's page even when search works.

**If Phase 0 fails, stop and report to Scott.** Do not build Phase 1.

## Files

| Path | Action | Responsibility |
|---|---|---|
| `clusters/vollminlab-cluster/mediastack/rreading-glasses/app/deployment.yaml` | Create | rreading-glasses + postgres sidecar |
| `clusters/vollminlab-cluster/mediastack/rreading-glasses/app/service.yaml` | Create | ClusterIP `rreading-glasses:80 → 8788` |
| `clusters/vollminlab-cluster/mediastack/rreading-glasses/app/pvc-rreading-glasses-cache.yaml` | Create | 2Gi Longhorn cache volume |
| `clusters/vollminlab-cluster/mediastack/rreading-glasses/app/rreading-glasses-hardcover-externalsecret.yaml` | Create | `HARDCOVER_AUTH` from 1Password |
| `clusters/vollminlab-cluster/mediastack/rreading-glasses/app/kustomization.yaml` | Create | resource list |
| `clusters/vollminlab-cluster/mediastack/kustomization.yaml` | Modify | add `./rreading-glasses/app` |
| `clusters/vollminlab-cluster/mediastack/readarr/app/configmap.yaml` | Modify | `METADATA_URL` env (fallback only, see Task 6) |
| `docs/cluster-reference.md` | Modify | inventory entry + PVC row (CI-enforced) |

**What is *not* needed, and why:**
- **No Flux index changes.** `mediastack` is a single Flux Kustomization (`mediastack-kustomization.yaml`), and this is a raw-manifest app with no chart source.
- **No HelmRelease.** There is no chart for rreading-glasses.
- **No NetworkPolicy.** `mediastack` has none today (`kubectl get netpol -n mediastack` returns nothing).
- **No Ingress or Shlink slug.** It is internal-only.
- **No CNPG cluster.** This Postgres is a disposable cache, not data. That is also why it is deliberately backed up by neither Velero nor VolSync (Task 3).

---

## Phase 0: Prove search quality locally (gate)

### Task 0: Hardcover API key → 1Password

**Who:** Scott, manually. Claude cannot create a Hardcover account token.

- [ ] **Step 1: Create the key.** Scott logs in at https://hardcover.app, goes to Settings → API, and creates a key. Choose the longest available expiry and note the expiry date.
- [ ] **Step 2: Save it to 1Password.** Create a Homelab vault item:
  - Title: `Hardcover API Key`
  - Tag: `Homelab`
  - Field `token`: the raw token **without** a leading `Bearer `. If Hardcover displays `Bearer eyJ…`, strip the prefix.
  - Field `expires`: the expiry date
  - Notes: `Referenced by ExternalSecret — do not rename fields`
- [ ] **Step 3: Verify Claude can read it** (prints the length only, never the value):

```bash
op item get "Hardcover API Key" --vault Homelab --fields token --reveal | tr -d '\n' | wc -c
```

Expected: a number greater than 100. A `0` or an error means the item or field name is wrong.

### Task 1: Run rreading-glasses locally on devsbx01

- [ ] **Step 1: Start Postgres and rreading-glasses on a private network.** Pin the same digests that Phase 1 deploys.

```bash
export RG_DIR=$(mktemp -d /tmp/rg-proof.XXXX); echo "$RG_DIR"
docker network create rg-proof
docker run -d --name rg-pg --network rg-proof \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=rreading-glasses \
  docker.io/library/postgres:17.11@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f
op item get "Hardcover API Key" --vault Homelab --fields token --reveal \
  | tr -d '\n' | sed 's/^/Bearer /' > "$RG_DIR/hc_auth"; chmod 644 "$RG_DIR/hc_auth"
docker run -d --name rg --network rg-proof -p 127.0.0.1:8788:8788 \
  -v "$RG_DIR/hc_auth:/run/hc_auth:ro" \
  -e HARDCOVER_AUTH_FILE=/run/hc_auth -e POSTGRES_HOST=rg-pg \
  docker.io/blampe/rreading-glasses:hardcover@sha256:3f017a51d9007b715303a20f481c822e4df66485fc9e5f57f6fdf1de840dc02f \
  serve
sleep 10; docker logs rg 2>&1 | tail -20
```

Expected: the logs show it listening on `:8788`, with no `400` (which means a missing `Bearer ` prefix, rreading-glasses #592) and no Postgres connection error.

- If it exits saying `serve` is an unknown command, the entrypoint already includes `serve`. Rerun without the trailing `serve` argument, and note which form worked: Task 3 must use the same one.
- The image is distroless and runs as uid 65532, which is why the auth file is `chmod 644`.

- [ ] **Step 2: Smoke-test one query.**

```bash
curl -s -m 30 -w '\n%{http_code} %{time_total}s\n' 'http://127.0.0.1:8788/search?q=the+blade+itself+abercrombie'
```

Expected: HTTP 200 in under 5s, and a JSON list of objects carrying `bookId`, `workId` and `author.id`. The same shape comes from `api.bookinfo.pro`, which was verified 2026-10-04. A `200` with `[]` is the #574/#595 failure: **stop and report.**

### Task 2: Measure search recall against the real library

The test: for each book that is actually in Scott's library, searching *title + author* should return that book's work. In bookshelf a "book" is a Hardcover work, so the search result's `workId` must equal the library's `foreignBookId`. The script also prints titles for Scott's own recent searches (taken from the Readarr logs) so he can eyeball them, and checks author completeness for bookshelf #134.

- [ ] **Step 1: Export the library** from the Readarr API.

```bash
K=$(op item get "Readarr API Key" --vault Homelab --fields credential --reveal)
P=$(kubectl get pod -n mediastack -l app=readarr -o name | head -1)
kubectl exec -n mediastack "$P" -c readarr -- curl -s -H "X-Api-Key: $K" http://localhost:8787/api/v1/book  > "$RG_DIR/books.json"
kubectl exec -n mediastack "$P" -c readarr -- curl -s -H "X-Api-Key: $K" http://localhost:8787/api/v1/author > "$RG_DIR/authors.json"
python3 -c "import json;print(len(json.load(open('$RG_DIR/books.json'))))"
```

Expected: `1491`, or close to it.

- [ ] **Step 2: Write the proof script** to `$RG_DIR/proof.py`.

```python
#!/usr/bin/env python3
"""Search-recall proof for a rreading-glasses instance against a Readarr library."""
import json, random, sys, time, urllib.parse, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788"
DIR = sys.argv[2]
SAMPLE = 40
PAUSE = 3.0  # free tier: 60 req/min, burst 10; each search fans out to several calls

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # timeout, connection reset
        return str(e), None

books = json.load(open(f"{DIR}/books.json"))
authors = {a["id"]: a for a in json.load(open(f"{DIR}/authors.json"))}
owned = [b for b in books if b.get("statistics", {}).get("bookFileCount")]
random.seed(20261004)
sample = random.sample(owned, min(SAMPLE, len(owned)))

hits, misses, errors = 0, [], []
for b in sample:
    name = authors[b["authorId"]]["authorName"]
    q = f'{b["title"].split(":")[0]} {name}'
    status, res = get("/search?q=" + urllib.parse.quote(q))
    if status != 200 or res is None:
        errors.append((q, status))
    elif any(str(r.get("workId")) == b["foreignBookId"] for r in res):
        hits += 1
    else:
        misses.append((q, b["foreignBookId"], len(res)))
    time.sleep(PAUSE)

print(f"\nRECALL {hits}/{len(sample)}  errors={len(errors)}  misses={len(misses)}")
for m in misses:
    print("  MISS", m)
for e in errors:
    print("  ERR ", e)

print("\nSCOTT'S RECENT QUERIES (top 5 titles each, verify by eye):")
for q in ["the blade itself", "before they are hanged", "confessions of a trash"]:
    status, res = get("/search?q=" + urllib.parse.quote(q))
    print(f"  [{q}] -> {status}, {len(res or [])} results")
    for r in (res or [])[:5]:
        _, w = get(f'/work/{r["workId"]}')
        title = (w or {}).get("Title") or (w or {}).get("title") or "?"
        print(f"      {title}  (work {r['workId']})")
        time.sleep(PAUSE)

print("\nAUTHOR COMPLETENESS (bookshelf #134), library count vs works served:")
for a in sorted(authors.values(), key=lambda a: -a["statistics"]["bookCount"])[:5]:
    status, res = get(f'/author/{a["foreignAuthorId"]}')
    works = (res or {}).get("Works") or (res or {}).get("works") or []
    print(f'  {a["authorName"]:25s} library={a["statistics"]["bookCount"]:4d} served={len(works)} ({status})')
    time.sleep(PAUSE)
```

- [ ] **Step 3: Run it.** It takes about 5 minutes because it is deliberately throttled.

```bash
python3 "$RG_DIR/proof.py" http://127.0.0.1:8788 "$RG_DIR" | tee "$RG_DIR/proof.out"
```

- [ ] **Step 4: Run the baseline** against the Goodreads-backed server, for comparison only. This run tells us whether a miss comes from the Hardcover data or from rreading-glasses itself.

```bash
python3 "$RG_DIR/proof.py" https://api.bookinfo.pro "$RG_DIR" | grep -E 'RECALL|MISS|ERR' > "$RG_DIR/baseline.out"
```

The baseline is expected to report *low* RECALL even if its search works well. Its IDs are Goodreads IDs, so they will never match the library's Hardcover IDs. Only its `errors` count and the by-eye titles are comparable.

- [ ] **Step 5: Apply the gate.**

| Result | Meaning | Action |
|---|---|---|
| RECALL ≥ 36/40, errors = 0, the 3 queries show the expected books | Search works | Go to Phase 1 |
| errors > 0, or any `[]` on a real title | Rate limit / #595 | **Stop.** Report the error lines. Retry once with `PAUSE = 6.0` to separate "free tier too small" from "broken" |
| RECALL < 36/40, errors = 0 | Ranking problem | **Stop.** Show Scott the MISS lines. Check whether a title-only or ISBN query finds them |
| Author `served` far below `library` | #134 data-side | Not a gate (#134 is client-side), but report it |

Ask Scott which book "confessions of a trash" was meant to find before judging that query.

- [ ] **Step 6: Tear down.**

```bash
docker rm -f rg rg-pg; docker network rm rg-proof; rm -f "$RG_DIR/hc_auth"
```

Keep `proof.out` and `baseline.out`; their numbers go in the PR body.

---

## Phase 1: Deploy (only after the Phase 0 gate passes)

### Task 3: Manifests

Work in the worktree `../wt-rreading-glasses` on branch `feat/rreading-glasses`.

- [ ] **Step 1: Check Longhorn capacity** (`.claude/rules/storage.md`). A 2Gi volume × 3 replicas needs 6Gi free.

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,SCHEDULABLE:.metadata.annotations.node\.longhorn\.io/longhorn-schedulable-storage'
kubectl get nodes.longhorn.io -n longhorn-system -o json | python3 -c "
import json,sys
for n in json.load(sys.stdin)['items']:
    for d,s in n['status'].get('diskStatus',{}).items():
        print(n['metadata']['name'], round((s['storageAvailable']-s['storageScheduled'])/2**30,1), 'GiB unscheduled')"
```

Expected: at least 3 nodes with ≥ 2 GiB unscheduled.

- [ ] **Step 2: Create `pvc-rreading-glasses-cache.yaml`.**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-rreading-glasses-cache
  namespace: mediastack
  labels:
    # Deliberately backed up by nothing. This is rreading-glasses' metadata
    # cache (one key/value table), rebuilt from Hardcover on demand. A Velero
    # FSB copy of a live Postgres data dir would be torn anyway. Excluded from
    # FSB by the pod annotation in deployment.yaml.
    app: rreading-glasses
    env: production
    category: media
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
  storageClassName: longhorn
```

- [ ] **Step 3: Create `rreading-glasses-hardcover-externalsecret.yaml`.** The template adds `Bearer `, because rreading-glasses rejects a bare token with HTTP 400 (#592).

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: rreading-glasses-hardcover
  namespace: mediastack
  labels:
    app: rreading-glasses
    env: production
    category: media
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: rreading-glasses-hardcover
    creationPolicy: Owner
    template:
      data:
        HARDCOVER_AUTH: "Bearer {{ .token }}"
  data:
    - secretKey: token
      remoteRef:
        key: "Hardcover API Key"
        property: token
```

- [ ] **Step 4: Create `deployment.yaml`.** Notes on the choices here:
  - Postgres is a sidecar listening on `127.0.0.1` only, so `trust` auth is unreachable from the pod network.
  - `PGDATA` is a subdirectory to avoid Longhorn's `lost+found`.
  - If Task 1 needed no `serve` argument, delete the `args` line.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rreading-glasses
  namespace: mediastack
  labels:
    app: rreading-glasses
    env: production
    category: media
    app.kubernetes.io/name: rreading-glasses
spec:
  replicas: 1
  strategy:
    type: Recreate   # RWO PVC — see .claude/rules/storage.md
  selector:
    matchLabels:
      app: rreading-glasses
  template:
    metadata:
      labels:
        app: rreading-glasses
        env: production
        category: media
        app.kubernetes.io/name: rreading-glasses
      annotations:
        # Cache only; a FSB copy of a live Postgres data dir is torn and useless.
        backup.velero.io/backup-volumes-excludes: "pgdata"
    spec:
      securityContext:
        fsGroup: 999
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: rreading-glasses
          image: docker.io/blampe/rreading-glasses:hardcover@sha256:3f017a51d9007b715303a20f481c822e4df66485fc9e5f57f6fdf1de840dc02f
          args: ["serve"]
          env:
            - name: POSTGRES_HOST
              value: "127.0.0.1"
            - name: POSTGRES_DATABASE
              value: rreading-glasses
          envFrom:
            - secretRef:
                name: rreading-glasses-hardcover
          ports:
            - name: http
              containerPort: 8788
          readinessProbe:
            tcpSocket:
              port: http
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests:
              cpu: 20m
              memory: 64Mi
            limits:
              # Go GOMEMLIMIT is derived as 85% of this; the in-memory cache grows to fill it.
              memory: 256Mi
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
        - name: postgres
          image: docker.io/library/postgres:17.11@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f
          args: ["-c", "listen_addresses=127.0.0.1"]
          env:
            - name: POSTGRES_HOST_AUTH_METHOD
              value: trust
            - name: POSTGRES_DB
              value: rreading-glasses
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
            - name: pgrun
              mountPath: /var/run/postgresql
          resources:
            requests:
              cpu: 20m
              memory: 64Mi
            limits:
              memory: 256Mi
          securityContext:
            runAsNonRoot: true
            runAsUser: 999
            runAsGroup: 999
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
      volumes:
        - name: pgdata
          persistentVolumeClaim:
            claimName: pvc-rreading-glasses-cache
        - name: pgrun
          emptyDir: {}
```

- [ ] **Step 5: Create `service.yaml`.**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: rreading-glasses
  namespace: mediastack
  labels:
    app: rreading-glasses
    env: production
    category: media
spec:
  type: ClusterIP
  selector:
    app: rreading-glasses
  ports:
    - name: http
      port: 80
      targetPort: 8788
```

- [ ] **Step 6: Create `kustomization.yaml`** and register it in the namespace index.

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: rreading-glasses-app
resources:
  - pvc-rreading-glasses-cache.yaml
  - rreading-glasses-hardcover-externalsecret.yaml
  - deployment.yaml
  - service.yaml
```

In `clusters/vollminlab-cluster/mediastack/kustomization.yaml`, add `- ./rreading-glasses/app` directly after `- ./readarr/app`.

- [ ] **Step 7: Validate locally.**

```bash
kubectl kustomize clusters/vollminlab-cluster/mediastack >/dev/null && echo BUILD-OK
kubectl apply --dry-run=server -k clusters/vollminlab-cluster/mediastack 2>&1 | grep -i rreading
```

Expected: `BUILD-OK`, and the rreading-glasses objects listed as `created (server dry run)` with no Kyverno denial. Then check the CI blind spot (`feedback_ci_integration_test_only_sees_helmreleases`): CI's integration test only dry-runs HelmReleases, so this server-side dry-run is the real admission check for raw manifests.

### Task 4: Docs (same PR; CI enforces it)

- [ ] **Step 1: Inventory section.** In `docs/cluster-reference.md`, add this after the `### FileBrowser (File drop)` section:

```markdown
### rreading-glasses (Readarr metadata)

| Parameter | Value |
|---|---|
| Image | `docker.io/blampe/rreading-glasses:hardcover` (digest-pinned; upstream publishes no version tags) |
| Sidecar | `postgres:17.11` (digest-pinned), `127.0.0.1` only, trust auth — cache store, not data |
| Service | `rreading-glasses.mediastack.svc:80` → 8788, internal only |
| Consumer | Readarr (`bookshelf:hardcover`) — `metadataSource` set in Readarr's DB, see plan Task 6 |
| Auth | Hardcover API key, 1P `Hardcover API Key` (field `token`, **expires** — see field `expires`) |
| Cache PVC | `pvc-rreading-glasses-cache` 2Gi Longhorn RWO — **deliberately unbacked** (rebuildable cache) |
| Why self-hosted | Shared `hardcover.bookinfo.pro` returned 429/timeouts on 11 of 11 searches, 2026-10-04 |
```

- [ ] **Step 2: PVC inventory.** Add a row to the `### PVC Inventory` table:

```markdown
| `pvc-rreading-glasses-cache` | mediastack | 2Gi | longhorn | RWO |
```

- [ ] **Step 3: Run the currency check.**

```bash
bash scripts/check-doc-currency.sh
```

Expected: exit 0.

### Task 5: Commit and PR

- [ ] **Step 1: Commit**, adding files explicitly by name:

```bash
git add clusters/vollminlab-cluster/mediastack/rreading-glasses/app/*.yaml \
        clusters/vollminlab-cluster/mediastack/kustomization.yaml \
        docs/cluster-reference.md docs/superpowers/plans/rreading-glasses.md
git commit -m "feat(mediastack): self-host rreading-glasses as Readarr's metadata source"
```

- [ ] **Step 2: Push and open the PR.** The body includes the Phase 0 RECALL line and error count, and no test-plan checklist. Give Scott the full PR URL. **Do not merge.** Scott merges.

---

## Phase 2: Switch Readarr over (after the merge reconciles)

### Task 6: Point Readarr at the new server

Readarr's own database holds `metadataSource = https://hardcover.bookinfo.pro`, which was verified 2026-10-04 via `GET /api/v1/config/development`. That database value **beats** the `METADATA_URL` env var (`ConfigService.cs` L265-287). Changing only the manifest therefore does nothing; the API call in Step 2 is the switch.

- [ ] **Step 1: Confirm the new pod is healthy and answering.**

```bash
kubectl get pod -n mediastack -l app=rreading-glasses
kubectl get externalsecret -n mediastack rreading-glasses-hardcover
kubectl run rg-check -n mediastack --rm -i --restart=Never --image=docker.io/curlimages/curl:8.11.1 \
  --labels=app=rg-check,env=production,category=media \
  --overrides='{"spec":{"containers":[{"name":"rg-check","image":"docker.io/curlimages/curl:8.11.1","args":["-s","-m","30","http://rreading-glasses/search?q=the+blade+itself+abercrombie"],"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"memory":"32Mi"}}}]}}'
```

Expected:
- the pod shows `2/2 Running`
- the ExternalSecret shows `SecretSynced` / `Ready=True`
- the curl returns a non-empty JSON list

- [ ] **Step 2: Switch the saved source.**

```bash
K=$(op item get "Readarr API Key" --vault Homelab --fields credential --reveal)
P=$(kubectl get pod -n mediastack -l app=readarr -o name | head -1)
kubectl exec -n mediastack "$P" -c readarr -- sh -c "
  curl -s -H 'X-Api-Key: $K' http://localhost:8787/api/v1/config/development \
  | sed 's#\"metadataSource\": *\"[^\"]*\"#\"metadataSource\": \"http://rreading-glasses.mediastack.svc.cluster.local\"#' \
  | curl -s -X PUT -H 'X-Api-Key: $K' -H 'Content-Type: application/json' -d @- http://localhost:8787/api/v1/config/development/1"
```

Expected: the response JSON shows the new `metadataSource`.

- [ ] **Step 3: Make the env fallback agree** (follow-up commit on a fresh branch). In `readarr/app/configmap.yaml`, under `workload.main.podSpec.containers.main.env`, add this next to `LSIO_NON_ROOT_USER`:

```yaml
                # Fallback only: Readarr's DB value (Settings → /settings/development)
                # wins over this. Both point at the self-hosted server.
                METADATA_URL: "http://rreading-glasses.mediastack.svc.cluster.local"
```

This keeps a rebuilt-from-scratch Readarr from silently reverting to the shared server.

### Task 7: Verify end to end, as Scott uses it

- [ ] **Step 1: Search through Readarr itself.** Run `"before they are hanged"` and two titles of Scott's choosing through Readarr's own search API. This exercises the real path, Readarr → rreading-glasses → Hardcover.

```bash
for q in "before they are hanged" "the blade itself"; do
  kubectl exec -n mediastack "$P" -c readarr -- curl -s -m 60 -G -H "X-Api-Key: $K" \
    --data-urlencode "term=$q" http://localhost:8787/api/v1/search \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(len(r), [ (x.get('book') or x.get('author') or {}).get('title') or (x.get('author') or {}).get('authorName') for x in r[:5]])"
done
```

Expected: non-empty results within a few seconds, with the target book among the first five.

- [ ] **Step 2: Check the logs are clean.**

```bash
kubectl logs -n mediastack "$P" -c readarr --since=10m | grep -cE '429|timed out|Unable to communicate'
```

Expected: `0`.

- [ ] **Step 3: Scott confirms in the UI.** Scott uses "Add New" in the Readarr UI to search for something he actually wants. That is the acceptance test, and it is his call, not ours.

---

## Known residuals (state these in the PR, don't fix here)

- **bookshelf #134:** an author's page may omit popular works with 3+ editions after a refresh. Search can still find them, and adding the book directly from search works. Bumping bookshelf to `hardcover-v0.4.21.182` does **not** fix it (nothing in `RefreshAuthorService` changed), so it stays out of this plan.
- **Hardcover key expiry:** search will fail again on the expiry date. The 1P `expires` field is the only record; nothing alerts on it. Consider a follow-up issue in the shape of `vcenter-credential-age`.
- **No version tags upstream:** Renovate cannot bump a digest-only `hardcover` tag meaningfully. Re-pin by hand when the upstream image changes.
