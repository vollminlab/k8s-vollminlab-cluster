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
        self.payloads, self.sweeps = [], 0

    def handle_alertmanager(self, payload):
        self.payloads.append(payload)
        return ["dispatched_new"]

    def handle_sweep(self):
        self.sweeps += 1
        return "dispatched_new"


@pytest.fixture
def server():
    d = FakeDispatcher()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


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
