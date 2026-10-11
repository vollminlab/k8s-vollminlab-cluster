import http.client
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from triage import sweep_tick
from triage.server import make_handler


class FakeDispatcher:
    def __init__(self):
        self.payloads, self.sweeps, self.raises = [], 0, None

    def handle_alertmanager(self, payload):
        if self.raises:
            raise self.raises
        self.payloads.append(payload)
        return ["dispatched_new"]

    def handle_sweep(self):
        self.sweeps += 1
        return "dispatched_new"


@pytest.fixture
def server():
    d = FakeDispatcher()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, threading.Lock()))
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield d, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def raw_post(base, path, body: bytes, content_length):
    """POST with a hand-set (or absent) Content-Length header."""
    host, port = base.rsplit("/", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", path, skip_accept_encoding=True)
    if content_length is not None:
        conn.putheader("Content-Length", content_length)
    conn.endheaders(body)
    resp = conn.getresponse()
    status = resp.status
    conn.close()
    return status


def post(url, body: bytes):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_alertmanager_webhook(server):
    d, base = server
    assert post(base + "/alertmanager", b'{"alerts": []}') == (200, ["dispatched_new"])
    assert d.payloads == [{"alerts": []}]


def test_bad_json_is_400_not_500(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/alertmanager", b"{nope")
    assert e.value.code == 400


def test_unknown_path_is_404_and_health_is_200(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/elsewhere", b"{}")
    assert e.value.code == 404
    with urllib.request.urlopen(base + "/healthz", timeout=5) as r:
        assert r.read() == b"ok"
    with urllib.request.urlopen(base + "/metrics", timeout=5) as r:
        assert r.status == 200


def test_sweep_tick_posts_to_the_dispatcher(server, monkeypatch, capsys):
    d, base = server
    monkeypatch.setenv("DISPATCHER_URL", base + "/sweep")
    assert sweep_tick.main() == 0
    assert d.sweeps == 1
    assert "HTTP 200" in capsys.readouterr().out


@pytest.mark.parametrize("length,expected", [("abc", 400), ("-1", 400), ("1.5", 400), ("\u00b2", 400),
                                             (str(10**9), 413)])
def test_bad_content_length_gets_a_response(server, length, expected):
    _, base = server
    assert raw_post(base, "/alertmanager", b"{}", length) == expected


def test_missing_content_length_is_an_empty_body(server):
    d, base = server
    assert raw_post(base, "/sweep", b"", None) == 200
    assert d.sweeps == 1
    assert raw_post(base, "/alertmanager", b"", None) == 400       # empty body is not JSON


def test_dispatcher_value_error_is_500_so_alertmanager_retries(server):
    d, base = server
    d.raises = ValueError("bad datetime from github")
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/alertmanager", b'{"alerts": []}')
    assert e.value.code == 500


def test_dispatcher_github_client_bounds_retry_waits(monkeypatch):
    # The dispatcher calls GitHub while holding its global lock, so a Retry-After
    # must not stall intake and scheduling for up to 60s per retry.
    from triage import server
    built = {}
    monkeypatch.setattr(server.GitHubApp, "from_dir",
                        classmethod(lambda cls, **kw: built.setdefault("app_kwargs", kw) and "app"))
    gh = server.dispatcher_github("triage-reports")
    assert gh.max_retry_after == server.DISPATCHER_MAX_RETRY_AFTER == 5
    assert built["app_kwargs"] == {"max_retry_after": 5}
    assert gh.token_provider.repositories == ["triage-reports"]
    assert gh.token_provider.permissions == {"issues": "write", "metadata": "read"}
