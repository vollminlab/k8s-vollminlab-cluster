"""Build triage Jobs from the template, and operate on the suspended-Job queue."""
import copy
import json
import posixpath

NAMESPACE = "triage"
FP_LABEL = "triage.vollminlab.com/fp"
KIND_LABEL = "triage.vollminlab.com/kind"
INCIDENT_ANNOTATION = "triage.vollminlab.com/incident"
# Set once the "job crashed" page has gone out, so a dispatcher restart cannot repeat it.
CRASH_NOTIFIED_ANNOTATION = "triage.vollminlab.com/crash-notified"
MIN_TTL_SECONDS = 86400
# The credential split: the LLM container never holds a GitHub or Pushover credential,
# and the container that writes to GitHub never holds the Anthropic key.
SECRET_OWNER = {
    "triage-anthropic-apikey": "investigate",
    "triage-github-app": "publish",
    "triage-pushover": "publish",
}
# The ONE volume both containers mount. investigate writes result.json there;
# publish mounts it read-only and reads nothing else from it.
RESULT_VOLUME = "result"


def _secret_volumes(pod: dict, secret: str) -> set:
    names = set()
    for v in pod.get("volumes", []):
        if (v.get("secret") or {}).get("secretName") == secret:
            names.add(v["name"])
        for src in (v.get("projected") or {}).get("sources", []):
            if (src.get("secret") or {}).get("name") == secret:
                names.add(v["name"])
    return names


def _uses_secret(pod: dict, container: dict, secret: str) -> bool:
    if {m["name"] for m in container.get("volumeMounts", [])} & _secret_volumes(pod, secret):
        return True
    if any(((e.get("valueFrom") or {}).get("secretKeyRef") or {}).get("name") == secret
           for e in container.get("env", [])):
        return True
    return any((e.get("secretRef") or {}).get("name") == secret for e in container.get("envFrom", []))


def _backing(v: dict) -> tuple:
    """What a volume is backed by. Two volume names over the same claim (or host path,
    ConfigMap, Secret) are one shared filesystem; an emptyDir is private to its name."""
    for kind, key in (("persistentVolumeClaim", "claimName"), ("hostPath", "path"), ("configMap", "name"),
                      ("secret", "secretName"), ("nfs", "path"), ("iscsi", "iqn")):
        if isinstance(v.get(kind), dict):
            return kind, str(v[kind].get(key))
    kind = next((k for k in v if k != "name"), "unknown")
    return kind, v["name"]


def _mounts(c: dict) -> dict:
    return {m["name"]: m for m in c.get("volumeMounts", [])}


def _env(c: dict, name: str):
    return next((e.get("value") for e in c.get("env", []) if e.get("name") == name), None)


ENTRYPOINTS = {"investigate": ["python", "-I", "-u", "-m", "triage.investigate"],
               "publish": ["python", "-I", "-u", "-m", "triage.publish"]}


def _check_containment(pod: dict, inv: dict, pub: dict) -> None:
    """investigate ran a possibly prompt-injected model, so nothing it could write may
    reach publish (which holds the GitHub key) except result.json: a shared checkout or
    HOME let it plant git config (filters, insteadOf, sshCommand), refs, or a Python
    .pth file that publish would then execute."""
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    im, pm = _mounts(inv), _mounts(pub)
    rv = volumes.get(RESULT_VOLUME)
    if rv is None or not isinstance(rv.get("emptyDir"), dict):
        raise ValueError(f"job template: volume {RESULT_VOLUME!r} must exist and be an emptyDir")
    if RESULT_VOLUME not in im or RESULT_VOLUME not in pm:
        raise ValueError(f"job template: volume {RESULT_VOLUME!r} must be mounted in both containers")
    if pm[RESULT_VOLUME].get("readOnly") is not True:
        raise ValueError(f"job template: volume {RESULT_VOLUME!r} must be readOnly: true in publish")
    if im[RESULT_VOLUME].get("readOnly"):
        raise ValueError(f"job template: volume {RESULT_VOLUME!r} must be writable in investigate")
    inv_backing = {_backing(volumes[n]): n for n in im if n in volumes}
    for name in pm:
        if name not in volumes:
            continue
        b = _backing(volumes[name])
        if b not in inv_backing or name == RESULT_VOLUME:
            continue
        other = inv_backing[b]
        # A ConfigMap is mounted read-only by the kubelet in every container, so it is
        # no channel from investigate to publish; the template must still say readOnly.
        if b[0] == "configMap" and im[other].get("readOnly") is True and pm[name].get("readOnly") is True:
            continue
        raise ValueError(f"job template: volume {name!r} ({b[0]}) is shared with investigate; only "
                         f"{RESULT_VOLUME!r} (and read-only ConfigMaps) may be shared")
    home = _env(pub, "HOME")
    if not home or not home.startswith("/"):
        raise ValueError("job template: publish must set HOME to an absolute path on its own volume")
    # Judge where the path lands, not how it is spelled: /publish/../result/home starts
    # with /publish but resolves onto the shared result volume.
    home = posixpath.normpath(home)
    owner = max((m for m in pm.values() if home == m["mountPath"] or home.startswith(m["mountPath"].rstrip("/") + "/")),
                key=lambda m: len(m["mountPath"]), default=None)
    if owner is None or owner["name"] == RESULT_VOLUME or owner.get("readOnly"):
        raise ValueError(f"job template: publish HOME {home!r} must be on a writable volume only publish mounts")
    if owner["name"] not in volumes or _backing(volumes[owner["name"]]) in inv_backing:
        raise ValueError(f"job template: publish HOME {home!r} is on a volume shared with investigate")
    for c in (inv, pub):
        if c.get("args"):
            raise ValueError(f"job template: {c['name']} must not set args (they are appended to the "
                             f"entrypoint, so `command: [python, -I]` + args could run any code)")
        if list(c.get("command") or []) != ENTRYPOINTS[c["name"]]:
            raise ValueError(f"job template: {c['name']} must run exactly {ENTRYPOINTS[c['name']]} "
                             f"(entrypoint; python -I is isolated: no user site-packages, no PYTHON* "
                             f"env, no cwd on sys.path)")


# Every volume type the Job may use. Anything else (hostPath, a PVC, csi, projected,
# downwardAPI, nfs, ...) either reaches outside the pod or is a second route around
# the credential split, which _secret_volumes only checks for plain secret volumes.
ALLOWED_VOLUME_TYPES = ("emptyDir", "configMap", "secret")
# Pod-level fields that would let one container see the other's processes (and so
# its environment, /proc/<pid>/root and open files) or the node's.
HOST_SHARING = ("shareProcessNamespace", "hostPID", "hostIPC", "hostNetwork")


def _check_isolation(pod: dict, containers: dict) -> None:
    """Volumes are emptyDir, configMap or secret only; no subPath mounts; env is literal
    values or secretKeyRef only; no shared process namespace, no host namespaces and
    no privileged container."""
    for v in pod.get("volumes", []):
        kinds = [k for k in v if k != "name"]
        if len(kinds) != 1 or kinds[0] not in ALLOWED_VOLUME_TYPES:
            raise ValueError(f"job template: volume {v.get('name')!r} is {'/'.join(kinds) or 'untyped'}; "
                             f"only emptyDir, configMap and secret volumes are allowed")
    for field in HOST_SHARING:
        if pod.get(field):
            raise ValueError(f"job template: {field} must not be set")
    for name, c in containers.items():
        for m in c.get("volumeMounts", []):
            for key in ("subPath", "subPathExpr"):
                if key in m:
                    raise ValueError(f"job template: {name} mounts {m.get('name')!r} with {key}; "
                                     f"subPath mounts are not allowed")
        if c.get("envFrom"):
            raise ValueError(f"job template: {name} uses envFrom; list each variable in env")
        for e in c.get("env", []):
            source = [k for k in (e.get("valueFrom") or {})]
            if source and source != ["secretKeyRef"]:
                raise ValueError(f"job template: {name} env {e.get('name')!r} uses valueFrom.{'/'.join(source)}; "
                                 f"only a literal value or secretKeyRef is allowed")
        if (c.get("securityContext") or {}).get("privileged"):
            raise ValueError(f"job template: {name} must not be privileged")


def check_template(t: dict) -> None:
    """Fail the dispatcher at startup, loudly, if the template drifted.

    The template is a ConfigMap, so neither kubeconform nor Kyverno sees it as a Job.
    """
    pod = t["spec"]["template"]["spec"]
    if [c["name"] for c in pod.get("initContainers", [])] != ["investigate"]:
        raise ValueError("job template: initContainers must be exactly [investigate]")
    if [c["name"] for c in pod.get("containers", [])] != ["publish"]:
        raise ValueError("job template: containers must be exactly [publish]")
    if pod.get("restartPolicy") != "Never":
        raise ValueError("job template: restartPolicy must be Never")
    if pod.get("serviceAccountName") != "triage-agent":
        raise ValueError("job template: serviceAccountName must be triage-agent")
    ttl = t["spec"].get("ttlSecondsAfterFinished")
    if not isinstance(ttl, int) or ttl < MIN_TTL_SECONDS:
        raise ValueError(f"job template: spec.ttlSecondsAfterFinished must be >= {MIN_TTL_SECONDS} "
                         f"(the dispatcher's 24h spend accounting reads finished Jobs), got {ttl!r}")
    containers = {c["name"]: c for c in pod["initContainers"] + pod["containers"]}
    for name, c in containers.items():
        names = [e.get("name") for e in c.get("env", [])]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            # the kubelet applies the last entry for a name; _env reads the first
            raise ValueError(f"job template: {name} has duplicate env names {dupes}")
    for secret, owner in SECRET_OWNER.items():
        holders = sorted(name for name, c in containers.items() if _uses_secret(pod, c, secret))
        if holders != [owner]:
            raise ValueError(f"job template: secret {secret} must be available to exactly [{owner}] "
                             f"(the credential split), but is in {holders}")
    _check_containment(pod, containers["investigate"], containers["publish"])
    _check_isolation(pod, containers)


def build_job(template: dict, incident, existing_issue, deadline_seconds: int, now: int) -> dict:
    job = copy.deepcopy(template)
    meta = job.setdefault("metadata", {})
    meta["name"] = f"triage-{incident.fp12}-{now}"
    labels = meta.setdefault("labels", {})
    labels[FP_LABEL] = incident.fp12
    labels[KIND_LABEL] = "sweep" if incident.source == "sweep" else "incident"
    meta.setdefault("annotations", {})[INCIDENT_ANNOTATION] = incident.to_json()
    spec = job["spec"]
    spec["suspend"] = True                       # the dispatcher's tick un-suspends one at a time
    spec["backoffLimit"] = 0                     # a retry would pay for the whole investigation again
    spec["activeDeadlineSeconds"] = deadline_seconds
    env = [{"name": "TRIAGE_INCIDENT", "value": incident.to_json()},
           {"name": "TRIAGE_EXISTING_ISSUE", "value": str(existing_issue or "")},
           # The same suffix as the Job name; publish puts it in the PR branch name.
           {"name": "TRIAGE_RUN", "value": str(now)}]
    pod = spec["template"]["spec"]
    for c in pod.get("initContainers", []) + pod["containers"]:
        c.setdefault("env", []).extend(copy.deepcopy(env))
    return job


def fp_of(job):
    return (job.metadata.labels or {}).get(FP_LABEL)


def is_suspended(job) -> bool:
    return bool(job.spec.suspend)


def finished(job) -> bool:
    return any(c.type in ("Complete", "Failed") and c.status == "True"
               for c in (job.status.conditions or []))


def finished_at(job):
    if job.status.completion_time:
        return job.status.completion_time
    for c in job.status.conditions or []:
        if c.type == "Failed" and c.status == "True":
            return c.last_transition_time
    return None


def read_outcome(core, namespace: str, job_name: str):
    """publish's termination message, or None if publish never ran (killed, OOM, deadline)."""
    pods = core.list_namespaced_pod(namespace, label_selector=f"job-name={job_name}").items
    for pod in pods:
        for cs in pod.status.container_statuses or []:
            term = cs.state.terminated if cs.state else None
            if cs.name == "publish" and term is not None and term.message:
                try:
                    return json.loads(term.message)
                except ValueError:
                    # cost unknown: the dispatcher charges the per-run cap
                    return {"outcome": "publish_failed", "cost_usd": None,
                            "detail": "unparseable termination message"}
    return None


class JobQueue:
    def __init__(self, batch, namespace: str = NAMESPACE):
        self.batch, self.namespace = batch, namespace

    def list(self):
        return self.batch.list_namespaced_job(self.namespace, label_selector=FP_LABEL).items

    def create(self, job: dict):
        self.batch.create_namespaced_job(self.namespace, job)

    def unsuspend(self, name: str):
        self.batch.patch_namespaced_job(name, self.namespace, {"spec": {"suspend": False}})

    def annotate(self, name: str, key: str, value: str):
        self.batch.patch_namespaced_job(name, self.namespace, {"metadata": {"annotations": {key: value}}})

    def delete(self, name: str):
        self.batch.delete_namespaced_job(name, self.namespace, propagation_policy="Background")
