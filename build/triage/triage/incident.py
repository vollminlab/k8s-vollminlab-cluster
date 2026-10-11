"""Normalize Alertmanager payloads into Incidents, and fingerprint them."""
import hashlib
import json
from dataclasses import asdict, dataclass, field

# The dispatcher never investigates its own namespace: a broken triage cannot be
# trusted to diagnose itself, and doing so could loop. Those alerts still reach
# Pushover through Alertmanager's normal routes.
SELF_NAMESPACE = "triage"

# Alert label -> workload kind, most specific owner first. `pod` comes after the
# controller labels because owners.resolve() walks a Pod up to its controller.
_WORKLOAD_LABELS = (
    ("cronjob", "CronJob"),
    ("job_name", "Job"),
    ("deployment", "Deployment"),
    ("statefulset", "StatefulSet"),
    ("daemonset", "DaemonSet"),
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
        return cls(**json.loads(text))


def namespace_of(labels: dict) -> str:
    """The object's namespace, not the scraper's.

    On kube-state-metrics-derived series (FluxResourceNotReady, KubeJobFailed, ...)
    `namespace` is where KSM runs (`monitoring`); the object's own namespace was
    renamed to `exported_namespace` to avoid the clash. Reading `namespace` would
    file every Flux failure as a `monitoring` incident.
    """
    return labels.get("exported_namespace") or labels.get("namespace", "")


def raw_workload(labels: dict) -> tuple:
    if labels.get("customresource_kind") and labels.get("name"):
        return labels["customresource_kind"], labels["name"]
    relabelled = "exported_namespace" in labels
    for label, kind in _WORKLOAD_LABELS:
        if relabelled and label == "pod":
            # On a relabelled series plain `pod` is the scraper's own pod.
            value = labels.get("exported_pod", "")
        else:
            value = labels.get(label, "")
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
            namespace=namespace_of(labels),
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
