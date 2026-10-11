import json

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from triage.ghapp import CachedToken, GitHub, GitHubApp


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
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
        return self.responses.pop(0)

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
        gh.create_issue("o/r", "t", "b", [])
    assert len(s.calls) == 3


def test_no_open_issue_returns_none():
    gh = GitHub(lambda: "t", session=FakeSession(Resp(200, [])), sleep=lambda _: None)
    assert gh.find_open_issue("o/r", "x") is None
