import json

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from triage.ghapp import CachedToken, GitHub, GitHubApp


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def _pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()


def test_token_is_scoped_and_signed_by_the_app():
    key, pem = _pem()
    s = FakeSession(Resp(201, {"token": "ghs_x"}))
    app = GitHubApp("123", "456", pem, session=s)
    assert app.token(["triage-reports"], {"issues": "write"}) == "ghs_x"
    method, url, kw = s.calls[0]
    assert url.endswith("/app/installations/456/access_tokens")
    assert kw["json"] == {"repositories": ["triage-reports"], "permissions": {"issues": "write"}}
    claims = jwt.decode(kw["headers"]["Authorization"].split()[1], key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "123"


def test_cached_token_refreshes_after_ttl():
    minted = []
    class App:
        def token(self, repos, perms):
            minted.append(1)
            return f"t{len(minted)}"
    now = [0.0]
    c = CachedToken(App(), ["r"], {}, ttl=3000, clock=lambda: now[0])
    assert c() == "t1" and c() == "t1"
    now[0] = 3001
    assert c() == "t2"


def test_retries_5xx_then_succeeds():
    sleeps = []
    s = FakeSession(Resp(502), Resp(200, [{"number": 7}]))
    gh = GitHub(lambda: "t", session=s, sleep=sleeps.append)
    assert gh.find_open_issue("vollminlab/triage-reports", "abc123def456") == {"number": 7}
    assert sleeps == [1]
    assert s.calls[1][2]["params"]["labels"] == "fp/abc123def456"
    assert s.calls[1][2]["headers"]["Authorization"] == "Bearer t"


def test_4xx_is_not_retried():
    s = FakeSession(Resp(404))
    gh = GitHub(lambda: "t", session=s, sleep=lambda _: None)
    with pytest.raises(requests.HTTPError):
        gh.comment("o/r", 1, "x")
    assert len(s.calls) == 1


def test_gives_up_after_retries():
    s = FakeSession(Resp(503), Resp(503), Resp(503))
    gh = GitHub(lambda: "t", session=s, sleep=lambda _: None)
    with pytest.raises(requests.HTTPError):
        gh.find_open_issue("o/r", "x")      # creates are not retried on 5xx at all
    assert len(s.calls) == 3


def test_no_open_issue_returns_none():
    gh = GitHub(lambda: "t", session=FakeSession(Resp(200, [])), sleep=lambda _: None)
    assert gh.find_open_issue("o/r", "x") is None


@pytest.mark.parametrize("exc", [requests.ConnectionError("reset"), requests.Timeout("read timed out")])
def test_connection_errors_and_timeouts_are_retried_on_safe_calls(exc):
    sleeps = []
    s = FakeSession(exc, Resp(200, []))
    gh = GitHub(lambda: "t", session=s, sleep=sleeps.append)
    assert gh.find_open_issue("o/r", "x") is None
    assert sleeps == [1] and len(s.calls) == 2


def test_connection_errors_give_up_after_retries():
    s = FakeSession(requests.ConnectionError("a"), requests.ConnectionError("b"), requests.ConnectionError("c"))
    gh = GitHub(lambda: "t", session=s, sleep=lambda _: None)
    with pytest.raises(requests.ConnectionError):
        gh.add_labels("o/r", 1, ["x"])      # idempotent, so retried; a comment is not
    assert len(s.calls) == 3


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_is_honoured(status):
    sleeps = []
    s = FakeSession(Resp(status, headers={"Retry-After": "7"}), Resp(200, []))
    GitHub(lambda: "t", session=s, sleep=sleeps.append).find_open_issue("o/r", "x")
    assert sleeps == [7]


def test_retry_after_is_capped_at_60s_and_garbage_falls_back():
    sleeps = []
    s = FakeSession(Resp(429, headers={"Retry-After": "3600"}),
                    Resp(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), Resp(200, []))
    GitHub(lambda: "t", session=s, sleep=sleeps.append).find_open_issue("o/r", "x")
    assert sleeps == [60, 2]


@pytest.mark.parametrize("call", [
    lambda gh: gh.create_issue("o/r", "t", "b", []),
    lambda gh: gh.create_pull("o/r", "t", "h", "main", "b"),
    # A comment is a create too: a re-POST after a 502 posts the report twice.
    lambda gh: gh.comment("o/r", 1, "b"),
])
@pytest.mark.parametrize("first", [Resp(502), Resp(503, headers={"Retry-After": "1"}),
                                   requests.Timeout("read timed out"), requests.ConnectionError("reset")])
def test_creates_are_never_reposted_after_an_ambiguous_failure(call, first):
    # GitHub can 502 (or the connection drop) AFTER creating the object; a re-POST duplicates it.
    s = FakeSession(first, Resp(201, {"number": 1}))
    gh = GitHub(lambda: "t", session=s, sleep=lambda _: None)
    with pytest.raises((requests.HTTPError, requests.RequestException)):
        call(gh)
    assert len(s.calls) == 1


def test_creates_still_retry_429():
    sleeps = []
    s = FakeSession(Resp(429, headers={"Retry-After": "3"}), Resp(201, {"number": 5}))
    gh = GitHub(lambda: "t", session=s, sleep=sleeps.append)
    assert gh.create_issue("o/r", "t", "b", []) == {"number": 5}
    assert sleeps == [3] and len(s.calls) == 2


@pytest.mark.parametrize("first", [Resp(502), Resp(429), requests.ConnectionError("reset")])
def test_token_minting_retries(first):
    _, pem = _pem()
    sleeps = []
    s = FakeSession(first, Resp(201, {"token": "ghs_y"}))
    app = GitHubApp("123", "456", pem, session=s, sleep=sleeps.append)
    assert app.token(["r"], {}) == "ghs_y"
    assert len(s.calls) == 2 and len(sleeps) == 1


def test_token_minting_does_not_retry_4xx():
    _, pem = _pem()
    s = FakeSession(Resp(401), Resp(201, {"token": "x"}))
    with pytest.raises(requests.HTTPError):
        GitHubApp("123", "456", pem, session=s, sleep=lambda _: None).token(["r"], {})
    assert len(s.calls) == 1


@pytest.mark.parametrize("retries", [0, -1])
def test_retries_must_be_at_least_one(retries):
    with pytest.raises(ValueError):
        GitHub(lambda: "t", session=FakeSession(), retries=retries)
    with pytest.raises(ValueError):
        GitHubApp("1", "2", "pem", session=FakeSession(), retries=retries)


# --- bounded waits: the dispatcher calls GitHub while holding its global lock ----------

def test_max_retry_after_caps_the_sleep():
    sleeps = []
    s = FakeSession(Resp(429, headers={"Retry-After": "60"}), Resp(503, headers={"Retry-After": "30"}),
                    Resp(200, []))
    GitHub(lambda: "t", session=s, sleep=sleeps.append, max_retry_after=5).find_open_issue("o/r", "x")
    assert sleeps == [5, 5]


def test_max_retry_after_also_caps_exponential_backoff():
    sleeps = []
    s = FakeSession(Resp(502), Resp(502), Resp(502), Resp(200, []))
    GitHub(lambda: "t", session=s, sleep=sleeps.append, retries=4, max_retry_after=3).find_open_issue("o/r", "x")
    assert sleeps == [1, 2, 3]


def test_token_minting_honours_max_retry_after():
    _, pem = _pem()
    sleeps = []
    s = FakeSession(Resp(429, headers={"Retry-After": "60"}), Resp(201, {"token": "ghs_y"}))
    GitHubApp("123", "456", pem, session=s, sleep=sleeps.append, max_retry_after=5).token(["r"], {})
    assert sleeps == [5]


@pytest.mark.parametrize("bad", [-1, "5"])
def test_max_retry_after_is_validated(bad):
    with pytest.raises(ValueError):
        GitHub(lambda: "t", session=FakeSession(), max_retry_after=bad)


def test_secondary_rate_limit_403_with_retry_after_is_retried_on_safe_calls():
    sleeps = []
    s = FakeSession(Resp(403, headers={"Retry-After": "4"}), Resp(200, [{"number": 3}]))
    gh = GitHub(lambda: "t", session=s, sleep=sleeps.append)
    assert gh.find_open_issue("o/r", "x") == {"number": 3}
    assert sleeps == [4] and len(s.calls) == 2


def test_plain_403_is_not_retried():
    s = FakeSession(Resp(403), Resp(200, []))
    with pytest.raises(requests.HTTPError):
        GitHub(lambda: "t", session=s, sleep=lambda _: None).find_open_issue("o/r", "x")
    assert len(s.calls) == 1


def test_secondary_rate_limit_403_is_not_retried_on_creates():
    s = FakeSession(Resp(403, headers={"Retry-After": "1"}), Resp(201, {"number": 1}))
    with pytest.raises(requests.HTTPError):
        GitHub(lambda: "t", session=s, sleep=lambda _: None).create_issue("o/r", "t", "b", [])
    assert len(s.calls) == 1
