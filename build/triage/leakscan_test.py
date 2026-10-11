import json
import subprocess

import pytest

from triage.leakscan import LeakScanError, redact, scan


class Gitleaks:
    def __init__(self, findings=(), rc=0, raw=None):
        self.findings, self.rc, self.cmd, self.raw = list(findings), rc, None, raw

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        report = cmd[cmd.index("--report-path") + 1]
        with open(report, "w") as f:
            f.write(self.raw if self.raw is not None else json.dumps(self.findings))
        return subprocess.CompletedProcess(cmd, self.rc, "", "boom" if self.rc else "")


def test_hits_are_mapped_back_to_their_text():
    g = Gitleaks([{"File": "/tmp/x/in/issue.txt", "Secret": "ghp_abc"}])
    assert scan({"issue": "token ghp_abc", "patch": "clean"}, g) == {"issue": ["ghp_abc"]}
    assert g.cmd[:2] == ["gitleaks", "dir"] and "--exit-code" in g.cmd


def test_clean_is_empty():
    assert scan({"issue": "nothing here"}, Gitleaks()) == {}


def test_scanner_failure_raises():
    with pytest.raises(LeakScanError):
        scan({"issue": "x"}, Gitleaks(rc=2))


def test_redact_longest_first():
    assert redact("a SECRET and SECRET2", ["SECRET", "SECRET2"]) == "a [REDACTED] and [REDACTED]"


@pytest.mark.parametrize("raw", ["null", "{}", "[1]", '["x"]', "[null]", '[{"File": "a.txt"}]',
                                 '[{"File": 1, "Secret": "s"}]', "not json"],
                         ids=["null", "object", "int", "string", "null-entry", "no-secret",
                              "non-string-file", "invalid-json"])
def test_malformed_report_raises_leakscanerror(raw):
    with pytest.raises(LeakScanError):
        scan({"issue": "x"}, Gitleaks(raw=raw))
