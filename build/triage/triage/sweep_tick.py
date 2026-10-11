"""Sweep CronJob entrypoint: ask the dispatcher to queue tonight's sweep.

Exits non-zero on any HTTP failure, so a dispatcher outage surfaces as a failed
Job (KubeJobFailed -> Pushover) instead of a silently skipped sweep.
"""
import os
import sys
import urllib.request


def main() -> int:
    url = os.environ.get("DISPATCHER_URL", "http://triage-dispatcher.triage.svc:8080/sweep")
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"sweep requested: HTTP {r.status} {r.read().decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
