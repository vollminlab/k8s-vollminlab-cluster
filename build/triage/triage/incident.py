"""Normalize Alertmanager payloads into Incidents, and fingerprint them."""
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields

# The dispatcher never investigates its own namespace: a broken triage cannot be
# trusted to diagnose itself, and doing so could loop. Those alerts still reach
# Pushover through Alertmanager's normal routes.
SELF_NAMESPACE = "triage"

# Alert label -> workload kind, most specific owner first. `pod` comes after the
# controller labels because owners.resolve() walks a Pod up to its controller.
# `pod` may also be the SCRAPER's pod rather than the subject; see _subject_pod.
_WORKLOAD_LABELS = (
    ("cronjob", "CronJob"),
    ("job_name", "Job"),
    ("deployment", "Deployment"),
    ("statefulset", "StatefulSet"),
    ("daemonset", "DaemonSet"),
    ("horizontalpodautoscaler", "HorizontalPodAutoscaler"),
    ("pod", "Pod"),
    ("persistentvolumeclaim", "PersistentVolumeClaim"),
    ("node", "Node"),
)


def fingerprint(*parts: str) -> str:
    # \x1f (unit separator) cannot appear in a label value, so ("a","bc") != ("ab","c").
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass
class Incident:
    source: str                 # "alertmanager" | "sweep"
    reason: str                 # alertname, or "nightly-sweep"
    namespace: str
    workload: str               # "Kind/name", or "" for cluster-level alerts
    labels: dict = field(default_factory=dict)
    started_at: str = ""
    am_fingerprints: list = field(default_factory=list)   # Alertmanager's own, for re-checks
    status: str = "firing"

    @property
    def fingerprint(self) -> str:
        # Deliberately excludes labels, timestamps and messages: five restarting
        # pods of one Deployment must be one incident.
        return fingerprint(self.source, self.reason, self.namespace, self.workload)

    @property
    def fp12(self) -> str:
        return self.fingerprint[:12]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Incident":
        # Unknown keys are dropped, so a Job built by one image version can be read
        # by another (the annotation outlives a dispatcher rollout).
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in json.loads(text).items() if k in known})


def namespace_of(labels: dict) -> str:
    """The object's namespace, not the scraper's.

    Stock kube-state-metrics series (KubePodCrashLooping, KubeJobFailed, ...) keep the
    object's namespace in `namespace`. A series that carries its own `namespace` which
    collides with the scrape target's, and is not scraped with honor_labels, has it
    renamed to `exported_namespace`, while `namespace` becomes the scraper's
    (`monitoring`). FluxResourceNotReady's custom-resource-state series are that case;
    reading `namespace` there would file every Flux failure as a `monitoring` incident.
    Node-level alerts are cluster-scoped and get no namespace (see from_alertmanager).
    """
    return labels.get("exported_namespace") or labels.get("namespace", "")


def _subject_pod(labels: dict) -> str:
    """The `pod` the alert is about, or "" if the only pod label is the scraper's."""
    if "exported_namespace" in labels:
        # On a relabelled series plain `pod` is the scraper's own pod.
        return labels.get("exported_pod", "")
    pod, service = labels.get("pod", ""), labels.get("service", "")
    if service and pod.startswith(service + "-"):
        # kube-state-metrics / node-exporter: `pod` is the exporter pod behind `service`
        # (e.g. kube-prometheus-stack-kube-state-metrics-65786d78c5-5fdvb), not the subject.
        return ""
    return pod


def raw_workload(labels: dict) -> tuple:
    if labels.get("customresource_kind") and labels.get("name"):
        return labels["customresource_kind"], labels["name"]
    if labels.get("job") == "node-exporter" and not labels.get("node") and labels.get("instance"):
        # node-exporter relabels `instance` to the node name; every other label on its
        # series (pod, service, namespace) describes the exporter DaemonSet.
        return "Node", labels["instance"]
    for label, kind in _WORKLOAD_LABELS:
        value = _subject_pod(labels) if label == "pod" else labels.get(label, "")
        if value:
            return kind, value
    return "", ""


def from_alertmanager(payload: dict) -> list:
    incidents = []
    for alert in payload.get("alerts", []):
        labels = dict(alert.get("labels", {}))
        kind, name = raw_workload(labels)
        incidents.append(Incident(
            source="alertmanager",
            reason=labels.get("alertname", "unknown"),
            # Nodes are cluster-scoped: a `namespace` on a node series is the scraper's.
            namespace="" if kind == "Node" else namespace_of(labels),
            workload=f"{kind}/{name}" if kind else "",
            labels=labels,
            started_at=alert.get("startsAt", ""),
            am_fingerprints=[alert["fingerprint"]] if alert.get("fingerprint") else [],
            status=alert.get("status", "firing"),
        ))
    return incidents


def sweep_incident(now_iso: str) -> Incident:
    return Incident(source="sweep", reason="nightly-sweep", namespace="", workload="", started_at=now_iso)


def sweep_finding_fingerprint(check: str, namespace: str, obj: str) -> str:
    return fingerprint("sweep", check, namespace, obj)
