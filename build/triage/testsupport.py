"""Fixtures shared by the *_test.py modules. Not collected by pytest (no test_ functions)."""
import copy
import difflib

from triage.policy import parse_policy

POLICY_RAW = {
    "model": "claude-opus-5-5",
    "max_turns": 40,
    "per_run_budget_usd": 3,
    "daily_budget_usd": 15,
    "queue_cap": 10,
    "reinvestigate_after_hours": 24,
    "job_deadline_seconds": 2400,
    "reports_repo": "vollminlab/triage-reports",
    "cluster_repo": "vollminlab/k8s-vollminlab-cluster",
    "classes": {
        "oom-memory-limit": "shadow",
        "cpu-throttle-limit": "shadow",
        "renovate-breakage": "shadow",
    },
    "renovate_denylist": ["velero-plugin-for-aws"],
    "sweep_checklist": "1. empty-selector-backup\n2. unprotected-pvc\n",
}


def make_policy(**overrides):
    raw = copy.deepcopy(POLICY_RAW)
    raw.update(overrides)
    return parse_policy(raw)


# A minimal Job template that passes jobs.check_template: the spec's credential split
# (Anthropic key only in investigate; GitHub App and Pushover only in publish) and a
# TTL long enough for the dispatcher's 24h spend accounting.
JOB_TEMPLATE = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"namespace": "triage", "labels": {"app": "triage-agent"}},
    "spec": {
        "ttlSecondsAfterFinished": 90000,
        "template": {"spec": {
            "serviceAccountName": "triage-agent",
            "restartPolicy": "Never",
            "initContainers": [{"name": "investigate",
                                "env": [{"name": "HOME", "value": "/work/home"}],
                                "volumeMounts": [{"name": "work", "mountPath": "/work"},
                                                 {"name": "anthropic", "mountPath": "/var/run/secrets/triage/anthropic"}]}],
            "containers": [{"name": "publish",
                            "volumeMounts": [{"name": "work", "mountPath": "/work"},
                                             {"name": "github", "mountPath": "/var/run/secrets/triage/github"},
                                             {"name": "pushover", "mountPath": "/var/run/secrets/triage/pushover"}]}],
            "volumes": [{"name": "work", "emptyDir": {}},
                        {"name": "anthropic", "secret": {"secretName": "triage-anthropic-apikey"}},
                        {"name": "github", "secret": {"secretName": "triage-github-app"}},
                        {"name": "pushover", "secret": {"secretName": "triage-pushover"}}],
        }},
    },
}


def am_alert(labels, status="firing", fingerprint="aaaa1111", starts="2026-10-11T10:00:00Z"):
    """One alert as Alertmanager's webhook (version 4) sends it."""
    return {
        "status": status,
        "labels": labels,
        "annotations": {"summary": "test"},
        "startsAt": starts,
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus/graph",
        "fingerprint": fingerprint,
    }


def am_payload(*alerts):
    return {
        "version": "4",
        "groupKey": "{}:{}",
        "status": "firing",
        "receiver": "triage",
        "groupLabels": {},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "https://alertmanager.vollminlab.com",
        "alerts": list(alerts),
    }


# A FluxResourceNotReady alert exactly as its custom-resource-state series is labelled.
# Unlike the stock KSM series above, this one's own `namespace` collided with the
# target's and was renamed: `namespace` and `pod` belong to KSM itself, and the
# HelmRelease lives in `exported_namespace`.
FLUX_LABELS = {
    "alertname": "FluxResourceNotReady",
    "customresource_group": "helm.toolkit.fluxcd.io",
    "customresource_kind": "HelmRelease",
    "exported_namespace": "mediastack",
    "name": "radarr",
    "namespace": "monitoring",
    "pod": "kube-prometheus-stack-kube-state-metrics-65786d78c5-5fdvb",
    "ready": "False",
    "reason": "UpgradeFailed",
    "severity": "warning",
}

# --- real series from this cluster's Prometheus (2026-10-11) -----------------------
# Stock kube-state-metrics and node-exporter series keep the OBJECT's namespace in
# `namespace`, but `pod`, `service`, `instance` and `job` describe the SCRAPER. The
# Alertmanager alert labels are the rule's output labels, which for these stock rules
# keep the series labels.
KSM_POD = "kube-prometheus-stack-kube-state-metrics-65786d78c5-5fdvb"
KSM_SERVICE = "kube-prometheus-stack-kube-state-metrics"

# kube_node_status_condition{condition="Ready", status="true"} == 0
NODE_NOT_READY_LABELS = {
    "alertname": "KubeNodeNotReady", "severity": "warning",
    "condition": "Ready", "container": "kube-state-metrics", "endpoint": "http",
    "instance": "172.18.218.45:8080", "job": "kube-state-metrics", "namespace": "monitoring",
    "node": "k8sworker02", "pod": KSM_POD, "service": KSM_SERVICE, "status": "true",
}

# node_filesystem_avail_bytes
NODE_FS_LABELS = {
    "alertname": "NodeFilesystemSpaceFillingUp", "severity": "warning",
    "container": "node-exporter", "device": "/dev/sdb", "endpoint": "http-metrics",
    "fstype": "ext4", "instance": "k8sworker01", "job": "node-exporter",
    "mountpoint": "/mnt/vm-lt", "namespace": "monitoring",
    "pod": "kube-prometheus-stack-prometheus-node-exporter-24mhm",
    "service": "kube-prometheus-stack-prometheus-node-exporter",
}

# kube_pod_container_status_restarts_total: here `pod` IS the subject.
KSM_CRASHLOOP_LABELS = {
    "alertname": "KubePodCrashLooping", "severity": "warning",
    "container": "engine-image-ei-75a03ec3", "endpoint": "http", "instance": "172.18.218.45:8080",
    "job": "kube-state-metrics", "namespace": "longhorn-system", "pod": "engine-image-ei-75a03ec3-ksn6d",
    "service": KSM_SERVICE, "uid": "0b6f3c1e-1d2a-4c5b-9e8f-7a6b5c4d3e2f",
}

# kube_horizontalpodautoscaler_* (KubeHpaMaxedOut and friends)
KSM_HPA_LABELS = {
    "alertname": "KubeHpaMaxedOut", "severity": "warning",
    "container": "kube-state-metrics", "endpoint": "http", "instance": "172.18.218.45:8080",
    "job": "kube-state-metrics", "namespace": "mediastack", "horizontalpodautoscaler": "radarr",
    "pod": KSM_POD, "service": KSM_SERVICE,
}

CRASHLOOP_LABELS = {
    "alertname": "KubePodCrashLooping",
    "namespace": "mediastack",
    "pod": "radarr-7d9c8b6f5-x2x9q",
    "container": "radarr",
    "severity": "warning",
}

# --- a tiny repo for diff-guard / publish tests -----------------------------------

APP = "clusters/vollminlab-cluster/demo/demo/app/"

CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: demo-values
  namespace: demo
data:
  values.yaml: |
    image:
      repository: ghcr.io/example/demo
      tag: 1.4.0
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        cpu: 500m
        memory: 256Mi
"""

HELMRELEASE = """apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: demo
  namespace: demo
spec:
  chart:
    spec:
      chart: demo
      version: 2.0.0
"""

REPO_FILES = {
    APP + "configmap.yaml": CONFIGMAP,
    APP + "helmrelease.yaml": HELMRELEASE,
    APP + "kustomization.yaml": "resources:\n  - helmrelease.yaml\n  - configmap.yaml\n",
    APP + "demo-db-externalsecret.yaml": "kind: ExternalSecret\nmetadata:\n  name: demo-db\n  namespace: demo\n",
    "clusters/vollminlab-cluster/other/other/app/configmap.yaml": CONFIGMAP.replace("namespace: demo", "namespace: other"),
}


def make_patch(path, old, new):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), "a/" + path, "b/" + path))


def write_repo(root, files=REPO_FILES):
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


# A structured_output that passes the agent schema.
AGENT_OK = {
    "summary": "demo is OOMKilled: limit 256Mi, measured peak 300Mi.",
    "root_cause": "The container's working set exceeds its memory limit.",
    "evidence": ["kubectl describe pod: Last State Terminated, Reason OOMKilled"],
    "confidence": "high",
    "class": "oom-memory-limit",
    "app_dir": APP,
    "patch": make_patch(APP + "configmap.yaml", CONFIGMAP, CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi")),
    "metrics": {"peak_memory_bytes": 300 * 2**20},
    "injection_suspected": False,
    "findings": [],
}
