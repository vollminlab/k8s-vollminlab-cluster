import requests

from triage.alertmanager import still_firing


class S:
    def __init__(self, alerts=None, error=None):
        self.alerts, self.error, self.params = alerts, error, None

    def get(self, url, params, timeout):
        if self.error:
            raise self.error
        self.params = params
        alerts = self.alerts
        class R:
            def raise_for_status(self):
                pass
            def json(self):
                return alerts
        return R()


def test_firing_when_any_fingerprint_is_active():
    s = S([{"fingerprint": "a"}, {"fingerprint": "b"}])
    assert still_firing(["z", "b"], s) is True
    assert s.params == {"active": "true", "silenced": "false", "inhibited": "false"}


def test_not_firing_when_resolved_or_silenced():
    assert still_firing(["z"], S([{"fingerprint": "a"}])) is False


def test_no_fingerprints_counts_as_firing():
    assert still_firing([], S(error=AssertionError("must not be called"))) is True


def test_alertmanager_unreachable_counts_as_firing():
    # Fail open: a missed re-check costs one investigation; a false "resolved" loses one.
    assert still_firing(["a"], S(error=requests.ConnectionError("down"))) is True
