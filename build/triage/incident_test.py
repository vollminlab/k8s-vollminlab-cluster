from testsupport import CRASHLOOP_LABELS, FLUX_LABELS, am_alert, am_payload
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
