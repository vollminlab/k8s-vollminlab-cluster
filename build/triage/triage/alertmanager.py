"""Ask Alertmanager what is still firing."""
import requests

URL = "http://kube-prometheus-stack-alertmanager.monitoring.svc:9093"


def active_alerts(session=requests, base: str = URL) -> list:
    """Every active, unsilenced, uninhibited alert, in the API v2 shape.

    Raises on any failure. The dispatcher's two callers fail in opposite directions
    (tick starts the Job anyway; a resolve still closes the issue), so the choice is
    theirs. An empty list means nothing is firing, so a malformed body must not become one.
    """
    r = session.get(f"{base}/api/v2/alerts",
                    params={"active": "true", "silenced": "false", "inhibited": "false"}, timeout=15)
    r.raise_for_status()
    alerts = r.json()
    if not isinstance(alerts, list):
        raise ValueError(f"alertmanager returned {type(alerts).__name__}, not a list of alerts")
    return alerts
