"""Build triage Jobs from the template, and operate on the suspended-Job queue."""
import copy
import json

NAMESPACE = "triage"
FP_LABEL = "triage.vollminlab.com/fp"
KIND_LABEL = "triage.vollminlab.com/kind"
INCIDENT_ANNOTATION = "triage.vollminlab.com/incident"
MIN_TTL_SECONDS = 86400
# The credential split: the LLM container never holds a GitHub or Pushover credential,
# and the container that writes to GitHub never holds the Anthropic key.
SECRET_OWNER = {
    "triage-anthropic-apikey": "investigate",
    "triage-github-app": "publish",
    "triage-pushover": "publish",
}


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
    for secret, owner in SECRET_OWNER.items():
        holders = sorted(name for name, c in containers.items() if _uses_secret(pod, c, secret))
        if holders != [owner]:
            raise ValueError(f"job template: secret {secret} must be available to exactly [{owner}] "
                             f"(the credential split), but is in {holders}")


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
           {"name": "TRIAGE_EXISTING_ISSUE", "value": str(existing_issue or "")}]
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
                    return {"outcome": "publish_failed", "cost_usd": 0.0,
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

    def delete(self, name: str):
        self.batch.delete_namespaced_job(name, self.namespace, propagation_policy="Background")
