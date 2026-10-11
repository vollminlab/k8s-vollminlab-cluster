from triage.pushover import Pushover


class S:
    def __init__(self):
        self.calls = []

    def post(self, url, data, timeout):
        self.calls.append(data)
        class R:
            def raise_for_status(self):
                pass
        return R()


def test_send_truncates_and_links():
    s = S()
    Pushover("tok", "usr", session=s).send("t" * 300, "m" * 2000, url="https://x")
    d = s.calls[0]
    assert (d["token"], d["user"], d["url"]) == ("tok", "usr", "https://x")
    assert len(d["title"]) == 250 and len(d["message"]) == 1024


def test_empty_message_is_not_rejected():
    s = S()
    Pushover("tok", "usr", session=s).send("t", "")
    assert s.calls[0]["message"] == "(no detail)"
    assert "url" not in s.calls[0]
