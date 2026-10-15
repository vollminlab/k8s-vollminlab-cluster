import json

from testsupport import (CRASHLOOP_LABELS, FLUX_LABELS, KSM_CRASHLOOP_LABELS, KSM_HPA_LABELS, KSM_POD, KSM_SERVICE,
                         NODE_FS_LABELS, NODE_NOT_READY_LABELS, am_alert, am_payload)
from triage.incident import (Incident, fingerprint, from_alertmanager, namespace_of,
                             raw_workload, sweep_finding_fingerprint, sweep_incident)


def test_crashloop_alert_becomes_pod_incident():
    [inc] = from_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="f1")))
    assert inc.source == "alertmanager"
    assert inc.reason == "KubePodCrashLooping"
    assert inc.namespace == "mediastack"
    assert inc.workload == "Pod/radarr-7d9c8b6f5-x2x9q"   # owners.resolve() lifts this later
    assert inc.am_fingerprints == ["f1"]
    assert inc.status == "firing"


def test_ksm_alert_uses_exported_namespace_not_scraper_namespace():
    [inc] = from_alertmanager(am_payload(am_alert(FLUX_LABELS)))
    assert inc.namespace == "mediastack"
    assert inc.workload == "HelmRelease/radarr"


def test_relabelled_series_ignores_the_scrapers_own_pod_label():
    labels = {"alertname": "X", "namespace": "monitoring", "exported_namespace": "velero",
              "pod": "kube-state-metrics-abc", "job_name": "velero-pvb-healer-123"}
    assert raw_workload(labels) == ("Job", "velero-pvb-healer-123")
    labels2 = {"alertname": "X", "namespace": "monitoring", "exported_namespace": "velero",
               "pod": "kube-state-metrics-abc", "exported_pod": "node-agent-xyz"}
    assert raw_workload(labels2) == ("Pod", "node-agent-xyz")


def test_namespace_falls_back_to_namespace_label():
    assert namespace_of({"namespace": "harbor"}) == "harbor"
    assert namespace_of({}) == ""


def test_cluster_level_alert_has_no_workload():
    [inc] = from_alertmanager(am_payload(am_alert({"alertname": "Watchdog"})))
    assert inc.workload == ""
    assert inc.namespace == ""


def test_resolved_status_is_carried():
    [inc] = from_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved")))
    assert inc.status == "resolved"


def test_fingerprint_ignores_volatile_fields():
    a = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr",
                 labels={"pod": "radarr-1"}, started_at="t1", am_fingerprints=["x"])
    b = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr",
                 labels={"pod": "radarr-2"}, started_at="t2", am_fingerprints=["y"])
    assert a.fingerprint == b.fingerprint
    assert len(a.fp12) == 12


def test_fingerprint_separator_prevents_collisions():
    assert fingerprint("a", "bc") != fingerprint("ab", "c")


def test_json_round_trip():
    inc = Incident("alertmanager", "R", "ns", "Deployment/x", labels={"a": "b"}, am_fingerprints=["f"])
    assert Incident.from_json(inc.to_json()) == inc


def test_sweep_incident_and_finding_fingerprints():
    s = sweep_incident("2026-10-11T07:30:00+00:00")
    assert (s.source, s.reason, s.namespace, s.workload) == ("sweep", "nightly-sweep", "", "")
    assert sweep_finding_fingerprint("unprotected-pvc", "ns", "PersistentVolumeClaim/a") != \
           sweep_finding_fingerprint("unprotected-pvc", "ns", "PersistentVolumeClaim/b")


# --- scraper labels are not the subject (real series, see testsupport) --------------

def test_node_not_ready_is_the_node_not_the_ksm_pod():
    [inc] = from_alertmanager(am_payload(am_alert(NODE_NOT_READY_LABELS)))
    assert inc.workload == "Node/k8sworker02"
    assert inc.namespace == ""          # Nodes are cluster-scoped; `monitoring` is KSM's


def test_node_exporter_alert_is_the_instance_node():
    [inc] = from_alertmanager(am_payload(am_alert(NODE_FS_LABELS)))
    assert (inc.workload, inc.namespace) == ("Node/k8sworker01", "")


def test_node_exporter_alerts_on_two_nodes_are_two_incidents():
    other = dict(NODE_FS_LABELS, instance="k8sworker03",
                 pod="kube-prometheus-stack-prometheus-node-exporter-9xk2p")
    a, b = from_alertmanager(am_payload(am_alert(NODE_FS_LABELS), am_alert(other)))
    assert b.workload == "Node/k8sworker03"
    assert a.fingerprint != b.fingerprint


def test_ksm_crashloop_keeps_the_subject_pod():
    [inc] = from_alertmanager(am_payload(am_alert(KSM_CRASHLOOP_LABELS)))
    assert (inc.workload, inc.namespace) == ("Pod/engine-image-ei-75a03ec3-ksn6d", "longhorn-system")


def test_ksm_hpa_alert_is_the_hpa():
    [inc] = from_alertmanager(am_payload(am_alert(KSM_HPA_LABELS)))
    assert (inc.workload, inc.namespace) == ("HorizontalPodAutoscaler/radarr", "mediastack")


def test_scraper_pod_is_ignored_when_it_is_the_service_pod():
    labels = {"namespace": "monitoring", "job": "kube-state-metrics",
              "service": "kube-prometheus-stack-kube-state-metrics",
              "pod": "kube-prometheus-stack-kube-state-metrics-65786d78c5-5fdvb"}
    assert raw_workload(labels) == ("", "")


def test_from_json_ignores_unknown_keys():
    inc = Incident("alertmanager", "R", "ns", "Deployment/x")
    raw = json.loads(inc.to_json())
    raw["added_in_a_later_image"] = {"x": 1}
    assert Incident.from_json(json.dumps(raw)) == inc


def test_app_metric_alert_keeps_its_own_pod_when_service_shares_the_name():
    # An app scraped through a same-named Service: `pod` is the subject, not a scraper.
    labels = {"alertname": "RadarrQueueStuck", "namespace": "mediastack", "job": "radarr",
              "service": "radarr", "pod": "radarr-7d9c8b6f5-x2x9q"}
    [inc] = from_alertmanager(am_payload(am_alert(labels)))
    assert (inc.workload, inc.namespace) == ("Pod/radarr-7d9c8b6f5-x2x9q", "mediastack")


def test_scraper_rule_applies_only_to_ksm_and_node_exporter_jobs():
    ksm = {"namespace": "monitoring", "job": "kube-state-metrics", "service": KSM_SERVICE, "pod": KSM_POD}
    assert raw_workload(ksm) == ("", "")
    ne = {"namespace": "monitoring", "job": "node-exporter", "node": "k8sworker01",
          "service": "kube-prometheus-stack-prometheus-node-exporter",
          "pod": "kube-prometheus-stack-prometheus-node-exporter-24mhm"}
    assert raw_workload(ne) == ("Node", "k8sworker01")
    no_job = {"namespace": "monitoring", "service": KSM_SERVICE, "pod": KSM_POD}
    assert raw_workload(no_job) == ("Pod", KSM_POD)


def test_alerts_on_one_workload_are_one_incident_whatever_their_alertname():
    # A crash-looping Deployment fires KubePodCrashLooping, KubePodNotReady and
    # KubeDeploymentReplicasMismatch together; the 2026-10-15 canary filed 10 issues for 2
    # workloads this way.
    a = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr")
    b = Incident("alertmanager", "KubeDeploymentReplicasMismatch", "mediastack", "Deployment/radarr")
    assert a.fingerprint == b.fingerprint


def test_the_same_workload_name_in_two_namespaces_is_two_incidents():
    a = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/x")
    b = Incident("alertmanager", "KubePodCrashLooping", "other", "Deployment/x")
    assert a.fingerprint != b.fingerprint


def test_cluster_level_alerts_are_still_told_apart_by_alertname():
    # Nothing but the alertname identifies an alert with no workload.
    a = Incident("alertmanager", "NodeDiskWriteLatencyHigh", "", "")
    b = Incident("alertmanager", "etcdDatabaseHighFragmentationRatio", "", "")
    assert a.fingerprint != b.fingerprint


def test_alertmanager_incident_lists_its_alertname():
    [inc] = from_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    assert inc.alerts == ["KubePodCrashLooping"]


def test_incident_from_an_older_image_has_no_alerts_list():
    # Queued Jobs outlive a dispatcher rollout; their annotation predates the field.
    old = '{"source": "alertmanager", "reason": "R", "namespace": "ns", "workload": "Deployment/x"}'
    assert Incident.from_json(old).alerts == []
