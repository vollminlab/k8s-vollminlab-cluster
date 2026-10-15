import pytest
import requests

from triage.alertmanager import active_alerts


class S:
    def __init__(self, alerts=None, error=None, status=200):
        self.alerts, self.error, self.status, self.params = alerts, error, status, None

    def get(self, url, params, timeout):
        if self.error:
            raise self.error
        self.params = params
        alerts, status = self.alerts, self.status
        class R:
            def raise_for_status(self):
                if status >= 400:
                    raise requests.HTTPError(f"{status}")
            def json(self):
                return alerts
        return R()


def test_returns_the_active_unsilenced_uninhibited_alerts():
    s = S([{"fingerprint": "a", "labels": {"alertname": "X"}}])
    assert active_alerts(s) == [{"fingerprint": "a", "labels": {"alertname": "X"}}]
    assert s.params == {"active": "true", "silenced": "false", "inhibited": "false"}


def test_unreachable_alertmanager_raises_for_the_caller_to_decide():
    # The two callers fail in opposite directions (start the Job; close the issue), so the
    # client must not pick one for them.
    with pytest.raises(requests.ConnectionError):
        active_alerts(S(error=requests.ConnectionError("down")))
    with pytest.raises(requests.HTTPError):
        active_alerts(S([], status=503))


def test_a_non_list_body_is_an_error_not_an_empty_alert_set():
    # An empty list means "nothing firing" and closes issues; garbage must not read as that.
    with pytest.raises(ValueError):
        active_alerts(S({"error": "x"}))
