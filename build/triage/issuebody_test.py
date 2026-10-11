from testsupport import AGENT_OK
from triage import issuebody
from triage.incident import Incident
from triage.result import failure, success

INC = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr")


def test_title():
    assert issuebody.incident_title(INC) == "[KubePodCrashLooping] mediastack/Deployment/radarr"
    assert issuebody.incident_title(Incident("alertmanager", "NodeDown", "", "")) == "[NodeDown] cluster"


def test_body_has_marker_diagnosis_gates_and_preview():
    preview = {"branch": "fix/b", "title": "fix(x): y", "body": "PRBODY", "patch": "--- a/x"}
    body = issuebody.render_incident(INC, success(AGENT_OK, 0.5, ["Bash: curl x"]), ["✓ g1"], None, preview,
                                     "shadow mode")
    assert body.startswith(f"<!-- fp:{INC.fingerprint} -->")
    assert AGENT_OK["summary"] in body and "✓ g1" in body
    assert "PRBODY" in body and "shadow mode" in body and "```diff" in body
    assert "Denied tool calls: 1" in body
    assert "$0.50" in body


def test_failed_run_shows_reason_not_diagnosis():
    body = issuebody.render_incident(INC, failure("timeout", "took too long"), [], None, None, "")
    assert "`timeout`" in body and "took too long" in body and "## Root cause" not in body


def test_injection_banner():
    body = issuebody.render_incident(INC, success(dict(AGENT_OK, injection_suspected=True), 0, []), [], None, None, "")
    assert "Injection suspected" in body


def test_finding():
    f = {"check": "unprotected-pvc", "namespace": "ns", "object": "PersistentVolumeClaim/a",
         "summary": "s", "detail": "d"}
    title, body = issuebody.render_finding(f, "f" * 64)
    assert title == "[sweep] unprotected-pvc: ns/PersistentVolumeClaim/a"
    assert body.startswith("<!-- fp:" + "f" * 64 + " -->") and "d" in body
