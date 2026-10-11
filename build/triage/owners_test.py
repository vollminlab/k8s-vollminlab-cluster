from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from triage.owners import kube_owner_lookup, resolve

CHAIN = {
    ("Pod", "radarr-7d9c8b6f5-x2x9q"): ("ReplicaSet", "radarr-7d9c8b6f5"),
    ("ReplicaSet", "radarr-7d9c8b6f5"): ("Deployment", "radarr"),
    ("Pod", "healer-29012-abcde"): ("Job", "healer-29012"),
    ("Job", "healer-29012"): ("CronJob", "healer"),
}


def lookup(kind, ns, name):
    return CHAIN.get((kind, name))


def test_pod_resolves_to_deployment():
    assert resolve("Pod", "mediastack", "radarr-7d9c8b6f5-x2x9q", lookup) == ("Deployment", "radarr")


def test_pod_resolves_to_cronjob():
    assert resolve("Pod", "velero", "healer-29012-abcde", lookup) == ("CronJob", "healer")


def test_unowned_pod_stays_a_pod():
    assert resolve("Pod", "x", "bare-pod", lookup) == ("Pod", "bare-pod")


def test_non_resolvable_kind_is_untouched():
    calls = []
    assert resolve("HelmRelease", "x", "y", lambda *a: calls.append(a)) == ("HelmRelease", "y")
    assert calls == []


def test_depth_limit_stops_cycles():
    loop = lambda kind, ns, name: ("ReplicaSet", name)
    assert resolve("Pod", "x", "p", loop, max_depth=3) == ("ReplicaSet", "p")


def _obj(*refs):
    return SimpleNamespace(metadata=SimpleNamespace(owner_references=list(refs)))


def test_kube_lookup_returns_controller_ref_and_404_is_none():
    ref = SimpleNamespace(kind="ReplicaSet", name="rs1", controller=True)
    other = SimpleNamespace(kind="Thing", name="t", controller=False)
    def read_pod(name, ns):
        if name == "gone":
            raise ApiException(status=404)
        return _obj(other, ref)
    core = SimpleNamespace(read_namespaced_pod=read_pod)
    get_owner = kube_owner_lookup(core, SimpleNamespace(), SimpleNamespace())
    assert get_owner("Pod", "ns", "p") == ("ReplicaSet", "rs1")
    assert get_owner("Pod", "ns", "gone") is None


def test_kube_lookup_reraises_other_errors():
    def read_pod(name, ns):
        raise ApiException(status=403)
    get_owner = kube_owner_lookup(SimpleNamespace(read_namespaced_pod=read_pod), SimpleNamespace(), SimpleNamespace())
    with pytest.raises(ApiException):
        get_owner("Pod", "ns", "p")
