"""Re-check an incident's alerts just before spending money on it."""
import logging

import requests

log = logging.getLogger("triage.alertmanager")
URL = "http://kube-prometheus-stack-alertmanager.monitoring.svc:9093"


def still_firing(am_fingerprints, session=requests, base: str = URL) -> bool:
    if not am_fingerprints:
        return True
    try:
        r = session.get(f"{base}/api/v2/alerts",
                        params={"active": "true", "silenced": "false", "inhibited": "false"}, timeout=15)
        r.raise_for_status()
        live = {a["fingerprint"] for a in r.json()}
    except requests.RequestException as e:
        log.warning("alertmanager re-check failed (%s); treating %s as still firing", e, am_fingerprints)
        return True
    return any(f in live for f in am_fingerprints)
