"""triage-query: the agent's only route to VictoriaMetrics and Loki."""
import http.server
import threading
import tomllib
from pathlib import Path

import pytest

from triage import query

HERE = Path(__file__).parent


def test_fixed_base_urls():
    assert query.TARGETS == {
        "vm": ("http://victoria-metrics-single-server.monitoring.svc:8428", "/api/v1/"),
        "vm-lt": ("http://victoria-metrics-lt-server.monitoring.svc:8428", "/api/v1/"),
        "loki": ("http://loki.monitoring.svc:3100", "/loki/api/v1/"),
    }


@pytest.mark.parametrize("target,path,url", [
    ("vm", "/api/v1/query?query=up", "http://victoria-metrics-single-server.monitoring.svc:8428/api/v1/query?query=up"),
    ("vm-lt", "/api/v1/query_range?query=up&start=-7d&step=1h",
     "http://victoria-metrics-lt-server.monitoring.svc:8428/api/v1/query_range?query=up&start=-7d&step=1h"),
    ("loki", "/loki/api/v1/query_range?query=%7Bnamespace%3D%22x%22%7D&limit=100",
     "http://loki.monitoring.svc:3100/loki/api/v1/query_range?query=%7Bnamespace%3D%22x%22%7D&limit=100"),
    # Characters a PromQL selector needs are percent-encoded rather than refused.
    ("vm", '/api/v1/query?query=up{namespace="a"}',
     "http://victoria-metrics-single-server.monitoring.svc:8428/api/v1/query?query=up%7Bnamespace=%22a%22%7D"),
])
def test_url_construction(target, path, url):
    assert query.build_url(target, path) == url


@pytest.mark.parametrize("target,path,why", [
    ("prometheus", "/api/v1/query?query=up", "target"),
    ("", "/api/v1/query", "target"),
    ("vm", "api/v1/query?query=up", "must start with"),
    ("vm", "/loki/api/v1/query", "must start with"),
    ("loki", "/api/v1/query", "must start with"),
    ("vm", "/api/v1x/query", "must start with"),
    ("vm", "/api/v1/../../metrics", ".."),
    ("vm", "/api/v1/query?query=a..b", ".."),
    ("vm", "/api/v1/query@evil.example/", "@"),
    ("vm", "/api/v1/query?u=a@b", "@"),
    ("vm", "/api/v1/query\\x", "\\"),
    ("vm", "/api/v1/query?query=up http://evil/", "whitespace"),
    ("vm", "/api/v1/query?query=up\tx", "whitespace"),
    ("vm", "/api/v1/query?query=up\nx", "whitespace"),
    ("vm", "/api/v1/query?query=up\x00", "control"),
    ("vm", "/api/v1/query?query=up\x7f", "control"),
    ("vm", "/api/v1/query?next=file:///var/run/secrets", "://"),
    ("vm", "/api/v1/query?query=up#frag", "#"),
    ("vm", "/api/v1/query#", "#"),
    ("vm", "/api/v1/admin/tsdb/delete_series?match[]=up", "admin"),
    ("vm", "/api/v1/%2e%2e/metrics", "endpoint"),
    ("vm", "/api/v1/query%3Fx", "endpoint"),
    ("vm", "/api/v1/", "endpoint"),
])
def test_rejections(target, path, why):
    with pytest.raises(query.QueryError) as e:
        query.build_url(target, path)
    assert why in str(e.value)


@pytest.mark.parametrize("argv", [[], ["vm"], ["vm", "/api/v1/query", "extra"],
                                  ["vm", "/api/v1/query", "http://loki.monitoring.svc:3100/x"]])
def test_exactly_two_arguments(argv, capsys):
    assert query.main(argv) == 1
    assert "usage: triage-query" in capsys.readouterr().err


def test_help_prints_usage_and_exits_zero(capsys):
    assert query.main(["--help"]) == 0
    assert "usage: triage-query" in capsys.readouterr().out


def test_no_override_comes_from_the_environment_or_arguments(monkeypatch):
    # The only way to point triage-query elsewhere is the test-only module attribute.
    monkeypatch.setenv("TRIAGE_QUERY_BASE", "http://evil")
    monkeypatch.setenv("http_proxy", "http://evil:1")
    monkeypatch.setenv("HTTP_PROXY", "http://evil:1")
    assert query._TEST_BASE is None
    assert query.build_url("vm", "/api/v1/query").startswith(
        "http://victoria-metrics-single-server.monitoring.svc:8428/")
    src = (HERE / "triage" / "query.py").read_text()
    assert "os.environ" not in src and "getenv" not in src


def test_opener_ignores_proxies_and_refuses_redirects():
    import urllib.request
    handlers = query._opener().handlers
    # An empty ProxyHandler registers no *_open method, so none survives in the opener;
    # the default (environment-reading) one must not have been added either.
    assert not [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert any(isinstance(h, query._RefuseRedirect) for h in handlers)


def test_console_script_is_declared():
    pyproject = tomllib.loads((HERE / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"] == {"triage-query": "triage.query:main"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.startswith("/api/v1/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/api/v1/stolen")
            self.end_headers()
        elif self.path.startswith("/api/v1/big"):
            body = b"x" * (query.MAX_BYTES + 4096)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/v1/bad"):
            body = b'{"status":"error","error":"parse error"}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b'{"status":"success"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.paths = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setattr(query, "_TEST_BASE", f"http://127.0.0.1:{srv.server_address[1]}")
    yield srv
    srv.shutdown()
    srv.server_close()


def test_get_prints_the_body(server, capsysbinary):
    assert query.main(["vm", "/api/v1/query?query=up"]) == 0
    assert capsysbinary.readouterr().out == b'{"status":"success"}'
    assert server.paths == ["/api/v1/query?query=up"]


def test_proxy_environment_is_ignored(server, monkeypatch, capsysbinary):
    for var in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:1")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert query.main(["vm", "/api/v1/query?query=up"]) == 0
    assert server.paths == ["/api/v1/query?query=up"]


def test_redirects_are_refused(server, capsysbinary):
    assert query.main(["vm", "/api/v1/redirect"]) == 1
    captured = capsysbinary.readouterr()
    assert b"redirect" in captured.err and captured.out == b""
    assert server.paths == ["/api/v1/redirect"]          # the Location was never followed


def test_output_is_capped_at_one_mib(server, capsysbinary):
    assert query.main(["vm", "/api/v1/big"]) == 0
    captured = capsysbinary.readouterr()
    assert len(captured.out) == query.MAX_BYTES
    assert b"truncated" in captured.err


def test_http_errors_exit_one_with_the_server_message(server, capsysbinary):
    assert query.main(["loki", "/loki/api/v1/../x"]) == 1        # refused before any request
    assert query.main(["vm", "/api/v1/bad"]) == 1
    err = capsysbinary.readouterr().err
    assert b"400" in err and b"parse error" in err
    assert server.paths == ["/api/v1/bad"]


def test_connection_errors_exit_one(monkeypatch, capsys):
    monkeypatch.setattr(query, "_TEST_BASE", "http://127.0.0.1:1")
    assert query.main(["vm", "/api/v1/query?query=up"]) == 1
    assert "triage-query:" in capsys.readouterr().err


def test_writes_no_files(server, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    query.main(["vm", "/api/v1/query?query=up"])
    query.main(["vm", "/api/v1/big"])
    assert list(tmp_path.iterdir()) == []
