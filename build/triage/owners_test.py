from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from triage.owners import MISSING, infer_owner, kube_owner_lookup, resolve

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
    get_owner = kube_owner_lookup(core, _api(), _api())
    assert get_owner("Pod", "ns", "p") == ("ReplicaSet", "rs1")
    assert get_owner("Pod", "ns", "gone") is None     # name fits no controller pattern


def test_kube_lookup_reraises_other_errors():
    def read_pod(name, ns):
        raise ApiException(status=403)
    get_owner = kube_owner_lookup(SimpleNamespace(read_namespaced_pod=read_pod), SimpleNamespace(), SimpleNamespace())
    with pytest.raises(ApiException):
        get_owner("Pod", "ns", "p")


# --- deleted objects: infer the owner from the name, adopt it only if it exists -----

def _exists(*present):
    calls = []
    def exists(kind, ns, name):
        calls.append((kind, name))
        return (kind, name) in present
    exists.calls = calls
    return exists


@pytest.mark.parametrize("kind,name,present,expected", [
    ("Pod", "radarr-7d9c8b6f5-x2x9q", ("Deployment", "radarr"), ("Deployment", "radarr")),
    ("Pod", "loki-0", ("StatefulSet", "loki"), ("StatefulSet", "loki")),
    ("Pod", "node-agent-x7k2p", ("DaemonSet", "node-agent"), ("DaemonSet", "node-agent")),
    ("ReplicaSet", "radarr-7d9c8b6f5", ("Deployment", "radarr"), ("Deployment", "radarr")),
    ("Job", "velero-pvb-healer-29312440", ("CronJob", "velero-pvb-healer"), ("CronJob", "velero-pvb-healer")),
])
def test_infer_owner_from_name_when_confirmed(kind, name, present, expected):
    assert infer_owner(kind, "ns", name, _exists(present)) == expected


def test_daemonset_is_tried_after_deployment():
    # "abc-7d9c8b6f5-x2x9q" fits both the Deployment and the DaemonSet pattern.
    exists = _exists(("DaemonSet", "abc-7d9c8b6f5"))
    assert infer_owner("Pod", "ns", "abc-7d9c8b6f5-x2x9q", exists) == ("DaemonSet", "abc-7d9c8b6f5")
    assert exists.calls[0] == ("Deployment", "abc")


@pytest.mark.parametrize("kind,name", [
    ("Pod", "radarr-7d9c8b6f5-x2x9q"), ("Pod", "loki-0"), ("Pod", "node-agent-x7k2p"),
    ("ReplicaSet", "radarr-7d9c8b6f5"), ("Job", "velero-pvb-healer-29312440"),
])
def test_inferred_controller_that_does_not_exist_is_not_adopted(kind, name):
    assert infer_owner(kind, "ns", name, _exists()) is None


def test_names_fitting_no_pattern_are_not_looked_up():
    exists = _exists()
    assert infer_owner("Pod", "ns", "bare", exists) is None
    assert infer_owner("Job", "ns", "manual-run", exists) is None
    assert exists.calls == []


def _api(**methods):
    return SimpleNamespace(**methods)


def _reader(objs):
    def read(name, ns):
        if name not in objs:
            raise ApiException(status=404)
        return objs[name]
    return read


def test_kube_lookup_infers_owner_of_a_deleted_pod_and_confirms_it():
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_replica_set=_reader({}),        # RS aged out too
                read_namespaced_deployment=_reader({"radarr": _obj()}),
                read_namespaced_stateful_set=_reader({}), read_namespaced_daemon_set=_reader({}))
    get_owner = kube_owner_lookup(core, apps, _api())
    assert get_owner("Pod", "mediastack", "radarr-7d9c8b6f5-x2x9q") == ("Deployment", "radarr")
    assert resolve("Pod", "mediastack", "radarr-7d9c8b6f5-x2x9q", get_owner) == ("Deployment", "radarr")
    # A pod whose inferred Deployment is also gone stays a Pod.
    assert resolve("Pod", "mediastack", "sonarr-7d9c8b6f5-x2x9q", get_owner) == ("Pod", "sonarr-7d9c8b6f5-x2x9q")


def test_kube_lookup_statefulset_and_daemonset_confirmations():
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_deployment=_reader({}),
                read_namespaced_stateful_set=_reader({"loki": _obj()}),
                read_namespaced_daemon_set=_reader({"node-agent": _obj()}))
    get_owner = kube_owner_lookup(core, apps, _api())
    assert get_owner("Pod", "ns", "loki-0") == ("StatefulSet", "loki")
    assert get_owner("Pod", "ns", "node-agent-x7k2p") == ("DaemonSet", "node-agent")


def test_kube_lookup_reads_replicasets():
    ref = SimpleNamespace(kind="Deployment", name="radarr", controller=True)
    apps = _api(read_namespaced_replica_set=_reader({"radarr-7d9c8b6f5": _obj(ref)}),
                read_namespaced_deployment=_reader({"sonarr": _obj()}))
    get_owner = kube_owner_lookup(_api(), apps, _api())
    assert get_owner("ReplicaSet", "ns", "radarr-7d9c8b6f5") == ("Deployment", "radarr")
    assert get_owner("ReplicaSet", "ns", "sonarr-6c5d4b3a2") == ("Deployment", "sonarr")   # RS gone
    assert get_owner("ReplicaSet", "ns", "lidarr-6c5d4b3a2") is None                       # both gone


def test_kube_lookup_reads_jobs():
    ref = SimpleNamespace(kind="CronJob", name="healer", controller=True)
    batch = _api(read_namespaced_job=_reader({"healer-29312440": _obj(ref)}),
                 read_namespaced_cron_job=_reader({"trim": _obj()}))
    get_owner = kube_owner_lookup(_api(), _api(), batch)
    assert get_owner("Job", "ns", "healer-29312440") == ("CronJob", "healer")
    assert get_owner("Job", "ns", "trim-29312440") == ("CronJob", "trim")        # Job gone
    assert get_owner("Job", "ns", "gone-29312440") is None                       # both gone


def test_confirming_read_errors_other_than_404_propagate():
    def forbidden(name, ns):
        raise ApiException(status=403)
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_replica_set=_reader({}), read_namespaced_deployment=forbidden)
    get_owner = kube_owner_lookup(core, apps, _api())
    with pytest.raises(ApiException):
        get_owner("Pod", "ns", "radarr-7d9c8b6f5-x2x9q")


# --- CronJob pods: <cronjob>-<schedule-index>-<5> -----------------------------------

def test_cronjob_pod_is_inferred_and_confirmed():
    assert infer_owner("Pod", "ns", "backup-29312440-abcde", _exists(("CronJob", "backup"))) == ("CronJob", "backup")


def test_cronjob_pattern_wins_over_a_same_named_deployment():
    # "backup-29312440-abcde" also fits the Deployment pattern (base "backup").
    exists = _exists(("CronJob", "backup"), ("Deployment", "backup"))
    assert infer_owner("Pod", "ns", "backup-29312440-abcde", exists) == ("CronJob", "backup")
    assert exists.calls[0] == ("CronJob", "backup")


def test_kube_lookup_confirms_a_deleted_cronjob_pod():
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_replica_set=_reader({}), read_namespaced_deployment=_reader({"backup": _obj()}),
                read_namespaced_stateful_set=_reader({}), read_namespaced_daemon_set=_reader({}))
    batch = _api(read_namespaced_cron_job=_reader({"backup": _obj()}))
    get_owner = kube_owner_lookup(core, apps, batch)
    assert resolve("Pod", "velero", "backup-29312440-abcde", get_owner) == ("CronJob", "backup")


# --- Deployment pods are confirmed through their ReplicaSet ---------------------------

def _rs_owner(rs):
    """replicaset_owner stub: rs maps name -> controller tuple (or None); absent = gone."""
    def owner(ns, name):
        return rs[name] if name in rs else MISSING
    return owner


def test_deployment_pod_confirmed_by_its_replicaset():
    exists = _exists()          # the Deployment read is not needed when the RS proves it
    owner = _rs_owner({"radarr-7d9c8b6f5": ("Deployment", "radarr")})
    assert infer_owner("Pod", "ns", "radarr-7d9c8b6f5-x2x9q", exists, owner) == ("Deployment", "radarr")


def test_daemonset_pod_is_not_pinned_on_a_same_prefix_deployment():
    # DaemonSet "foo-agentx" pod "foo-agentx-abcde" fits the Deployment pattern with
    # base "foo" and hash "agentx". No ReplicaSet "foo-agentx" exists, and "agentx" is
    # too short to be a pod-template-hash, so Deployment "foo" is not adopted.
    exists = _exists(("Deployment", "foo"), ("DaemonSet", "foo-agentx"))
    assert infer_owner("Pod", "ns", "foo-agentx-abcde", exists, _rs_owner({})) == ("DaemonSet", "foo-agentx")
    assert ("Deployment", "foo") not in exists.calls


def test_replicaset_owned_by_something_else_rejects_the_deployment():
    exists = _exists(("Deployment", "foo"), ("DaemonSet", "foo-7d9c8b6f5"))
    owner = _rs_owner({"foo-7d9c8b6f5": ("Deployment", "other")})
    assert infer_owner("Pod", "ns", "foo-7d9c8b6f5-abcde", exists, owner) == ("DaemonSet", "foo-7d9c8b6f5")


def test_replicaset_without_a_controller_rejects_the_deployment():
    exists = _exists(("Deployment", "foo"))
    owner = _rs_owner({"foo-7d9c8b6f5": None})
    assert infer_owner("Pod", "ns", "foo-7d9c8b6f5-abcde", exists, owner) is None


def test_gone_replicaset_falls_back_to_deployment_when_hash_looks_like_a_pod_template_hash():
    exists = _exists(("Deployment", "radarr"))
    assert infer_owner("Pod", "ns", "radarr-7d9c8b6f5-x2x9q", exists, _rs_owner({})) == ("Deployment", "radarr")


def test_gone_replicaset_with_a_short_hash_does_not_fall_back():
    exists = _exists(("Deployment", "radarr"))
    assert infer_owner("Pod", "ns", "radarr-7d9c8b-x2x9q", exists, _rs_owner({})) is None


def test_kube_lookup_confirms_deployment_through_a_live_replicaset():
    ref = SimpleNamespace(kind="Deployment", name="radarr", controller=True)
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_replica_set=_reader({"radarr-7d9c8b6f5": _obj(ref)}),
                read_namespaced_deployment=_reader({}),          # not consulted
                read_namespaced_stateful_set=_reader({}), read_namespaced_daemon_set=_reader({}))
    get_owner = kube_owner_lookup(core, apps, _api())
    assert get_owner("Pod", "ns", "radarr-7d9c8b6f5-x2x9q") == ("Deployment", "radarr")


def test_kube_lookup_daemonset_pod_with_deployment_shaped_name():
    core = _api(read_namespaced_pod=_reader({}))
    apps = _api(read_namespaced_replica_set=_reader({}), read_namespaced_deployment=_reader({"foo": _obj()}),
                read_namespaced_stateful_set=_reader({}),
                read_namespaced_daemon_set=_reader({"foo-agentx": _obj()}))
    get_owner = kube_owner_lookup(core, apps, _api())
    assert get_owner("Pod", "ns", "foo-agentx-abcde") == ("DaemonSet", "foo-agentx")


def test_replicaset_confirming_read_errors_other_than_404_propagate():
    def forbidden(name, ns):
        raise ApiException(status=403)
    core = _api(read_namespaced_pod=_reader({}))
    get_owner = kube_owner_lookup(core, _api(read_namespaced_replica_set=forbidden), _api())
    with pytest.raises(ApiException):
        get_owner("Pod", "ns", "radarr-7d9c8b6f5-x2x9q")


def test_name_candidates_lists_every_fit_unconfirmed():
    from triage.owners import name_candidates
    assert name_candidates("Pod", "radarr-7d9c8b6f5-x2x9q") == [
        ("Deployment", "radarr"), ("DaemonSet", "radarr-7d9c8b6f5")]
    assert name_candidates("Pod", "backup-29345760-abcde")[0] == ("CronJob", "backup")
    assert name_candidates("Job", "backup-29345760") == [("CronJob", "backup")]
    assert name_candidates("Deployment", "radarr") == []
