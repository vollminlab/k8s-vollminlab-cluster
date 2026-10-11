"""Walk a Pod, Job or ReplicaSet up to the controller that owns it."""
import re

from kubernetes.client.exceptions import ApiException

RESOLVABLE = ("Pod", "Job", "ReplicaSet")

# When the object is already gone (crash-looping, evicted and replaced, or a finished
# Job's pod garbage-collected), its owner is guessed from the generated name, in this
# order, and adopted only if that controller is confirmed to exist. Without this, the
# same incident fingerprints as Deployment/x while the pod lives and Pod/x-abc-123 once
# it is replaced, and dedup misses it.
_NAME_PATTERNS = {
    "Pod": (
        (re.compile(r"^(?P<base>.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$"), "Deployment"),
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


def infer_owner(kind: str, namespace: str, name: str, exists):
    """The controller a deleted `kind/name` most likely belonged to, or None.

    `exists(kind, namespace, name) -> bool` confirms each candidate; a guess that
    does not exist is never adopted.
    """
    for pattern, owner_kind in _NAME_PATTERNS.get(kind, ()):
        m = pattern.match(name)
        if m and exists(owner_kind, namespace, m["base"]):
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

    def get_owner(kind, namespace, name):
        try:
            obj = readers[kind](name, namespace)
        except ApiException as e:
            if e.status == 404:
                return infer_owner(kind, namespace, name, exists)
            raise
        for ref in obj.metadata.owner_references or []:
            if ref.controller:
                return ref.kind, ref.name
        return None

    return get_owner
