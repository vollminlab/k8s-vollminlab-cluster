"""Walk a Pod, Job or ReplicaSet up to the controller that owns it."""
import re

from kubernetes.client.exceptions import ApiException

RESOLVABLE = ("Pod", "Job", "ReplicaSet")

# Returned by a replicaset_owner callable when the ReplicaSet does not exist.
MISSING = object()

# A Deployment's pod-template-hash, as it appears in a ReplicaSet name. Used only when
# that ReplicaSet is gone; see _confirm_deployment_pod.
_POD_TEMPLATE_HASH = re.compile(r"^[a-z0-9]{8,10}$")

# When the object is already gone (crash-looping, evicted and replaced, or a finished
# CronJob run's pod garbage-collected), its owner is guessed from the generated name, in
# this order, and adopted only if that controller is confirmed to exist. Without this,
# the same incident fingerprints as Deployment/x while the pod lives and Pod/x-abc-123
# once it is replaced, and dedup misses it.
#
# A CronJob's pod is <cronjob>-<scheduled-minute-index>-<5>. It is tried before the
# Deployment pattern, which it also fits (base <cronjob>, hash <index>). A pod of a
# bare Job (no CronJob) cannot be inferred: <job>-<5> is indistinguishable from a
# DaemonSet pod, and a deleted bare Job is not confirmable anyway.
_NAME_PATTERNS = {
    "Pod": (
        (re.compile(r"^(?P<base>.+)-\d{8,}-[a-z0-9]{5}$"), "CronJob"),
        (re.compile(r"^(?P<base>.+)-(?P<hash>[a-z0-9]{6,10})-[a-z0-9]{5}$"), "Deployment"),
        (re.compile(r"^(?P<base>.+)-\d+$"), "StatefulSet"),
        (re.compile(r"^(?P<base>.+)-[a-z0-9]{5}$"), "DaemonSet"),
    ),
    "ReplicaSet": ((re.compile(r"^(?P<base>.+)-[a-z0-9]{6,10}$"), "Deployment"),),
    "Job": ((re.compile(r"^(?P<base>.+)-\d{8,}$"), "CronJob"),),
}


def resolve(kind: str, namespace: str, name: str, get_owner, max_depth: int = 4) -> tuple:
    for _ in range(max_depth):
        if kind not in RESOLVABLE:
            break
        owner = get_owner(kind, namespace, name)
        if owner is None:
            break
        kind, name = owner
    return kind, name


def _gone(namespace, name):
    return MISSING


def _confirm_deployment_pod(namespace: str, base: str, pod_hash: str, exists, replicaset_owner) -> bool:
    """Whether a deleted pod <base>-<pod_hash>-<5> belonged to Deployment <base>.

    The pod's own ReplicaSet <base>-<pod_hash> is the evidence: a Deployment <base>
    merely existing is not, because a DaemonSet `foo-agentx` names its pods
    `foo-agentx-abcde`, which fit this pattern with base `foo`.

    The ReplicaSet is normally gone only after enough rollouts to age it out of the
    Deployment's revisionHistoryLimit. Then the Deployment's existence is accepted,
    but only when the segment is shaped like a pod-template-hash (8-10 chars).
    Residual ambiguity, accepted: a DaemonSet whose name ends in an 8-10 character
    segment (`foo-abcdefgh`), whose pod is gone, next to a Deployment `foo`, is still
    pinned on the Deployment.
    """
    owner = replicaset_owner(namespace, f"{base}-{pod_hash}")
    if owner is not MISSING:
        return owner == ("Deployment", base)
    return bool(_POD_TEMPLATE_HASH.match(pod_hash)) and exists("Deployment", namespace, base)


def infer_owner(kind: str, namespace: str, name: str, exists, replicaset_owner=_gone):
    """The controller a deleted `kind/name` most likely belonged to, or None.

    `exists(kind, namespace, name) -> bool` confirms each candidate; a guess that
    does not exist is never adopted. `replicaset_owner(namespace, name)` returns a
    ReplicaSet's controller as (kind, name), None if it has none, or MISSING if the
    ReplicaSet does not exist; it confirms a Pod's Deployment.
    """
    for pattern, owner_kind in _NAME_PATTERNS.get(kind, ()):
        m = pattern.match(name)
        if not m:
            continue
        if kind == "Pod" and owner_kind == "Deployment":
            confirmed = _confirm_deployment_pod(namespace, m["base"], m["hash"], exists, replicaset_owner)
        else:
            confirmed = exists(owner_kind, namespace, m["base"])
        if confirmed:
            return owner_kind, m["base"]
    return None


def kube_owner_lookup(core, apps, batch):
    readers = {
        "Pod": lambda name, ns: core.read_namespaced_pod(name, ns),
        "ReplicaSet": lambda name, ns: apps.read_namespaced_replica_set(name, ns),
        "Job": lambda name, ns: batch.read_namespaced_job(name, ns),
    }
    confirmers = {
        "Deployment": lambda name, ns: apps.read_namespaced_deployment(name, ns),
        "StatefulSet": lambda name, ns: apps.read_namespaced_stateful_set(name, ns),
        "DaemonSet": lambda name, ns: apps.read_namespaced_daemon_set(name, ns),
        "CronJob": lambda name, ns: batch.read_namespaced_cron_job(name, ns),
    }

    def exists(kind, namespace, name):
        try:
            confirmers[kind](name, namespace)
        except ApiException as e:
            if e.status == 404:
                return False
            raise
        return True

    def controller_of(obj):
        for ref in obj.metadata.owner_references or []:
            if ref.controller:
                return ref.kind, ref.name
        return None

    def replicaset_owner(namespace, name):
        try:
            return controller_of(readers["ReplicaSet"](name, namespace))
        except ApiException as e:
            if e.status == 404:
                return MISSING
            raise

    def get_owner(kind, namespace, name):
        try:
            obj = readers[kind](name, namespace)
        except ApiException as e:
            if e.status == 404:
                return infer_owner(kind, namespace, name, exists, replicaset_owner)
            raise
        return controller_of(obj)

    return get_owner
