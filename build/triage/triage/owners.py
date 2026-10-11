"""Walk a Pod, Job or ReplicaSet up to the controller that owns it."""
from kubernetes.client.exceptions import ApiException

RESOLVABLE = ("Pod", "Job", "ReplicaSet")


def resolve(kind: str, namespace: str, name: str, get_owner, max_depth: int = 4) -> tuple:
    for _ in range(max_depth):
        if kind not in RESOLVABLE:
            break
        owner = get_owner(kind, namespace, name)
        if owner is None:
            break
        kind, name = owner
    return kind, name


def kube_owner_lookup(core, apps, batch):
    readers = {
        "Pod": lambda name, ns: core.read_namespaced_pod(name, ns),
        "ReplicaSet": lambda name, ns: apps.read_namespaced_replica_set(name, ns),
        "Job": lambda name, ns: batch.read_namespaced_job(name, ns),
    }

    def get_owner(kind, namespace, name):
        try:
            obj = readers[kind](name, namespace)
        except ApiException as e:
            if e.status == 404:
                # The pod is already gone (common for crash-looping or evicted pods).
                # The incident is then fingerprinted on the Pod itself.
                return None
            raise
        for ref in obj.metadata.owner_references or []:
            if ref.controller:
                return ref.kind, ref.name
        return None

    return get_owner
