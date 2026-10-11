"""triage-query: the investigate agent's only route to VictoriaMetrics and Loki.

    triage-query vm|vm-lt|loki <path?query>

It replaces `curl` in the agent's allowlist. A `Bash(curl -s http://loki.../*)` rule
also matched a SECOND URL on the same command line (a `file://` path to a mounted
secret, or an outside host), and combined short flags slipped past the write denies.
Here the host is fixed per target name and nothing on the command line can change it:
exactly two arguments, a path under the target's read API, no redirects followed, no
proxy honoured, at most MAX_BYTES printed, and no file written. Nothing is read from
the environment.
"""
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

TARGETS = {
    "vm": ("http://victoria-metrics-single-server.monitoring.svc:8428", "/api/v1/"),
    "vm-lt": ("http://victoria-metrics-lt-server.monitoring.svc:8428", "/api/v1/"),
    "loki": ("http://loki.monitoring.svc:3100", "/loki/api/v1/"),
}
USAGE = ("usage: triage-query vm|vm-lt|loki <path?query>\n"
         "  e.g. triage-query vm '/api/v1/query?query=up{namespace=\"velero\"}'\n"
         "       triage-query loki '/loki/api/v1/query_range?query={namespace=\"velero\"}&limit=100&since=1h'\n"
         "  Encode spaces as %20. Paths must start with /api/v1/ (vm, vm-lt) or /loki/api/v1/ (loki).")
TIMEOUT = 30
MAX_BYTES = 1 << 20
# Characters left as they are when the path is percent-encoded: RFC 3986 reserved
# characters (minus '#' and '@', which are refused) and '%', so an already-encoded
# query passes through unchanged. Anything else (`{`, `"`, `|`, non-ASCII) is encoded.
_SAFE = "/?&=%:,;+!$'()*[]~"
# The endpoint part (before '?'): plain path characters only, so no encoded traversal.
_ENDPOINT = re.compile(r"[A-Za-z0-9_\-./]+")

# TEST HOOK ONLY: a local server standing in for every target. Never set from the
# environment or the command line.
_TEST_BASE = None


class QueryError(ValueError):
    pass


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect refused (to {newurl[:200]})", headers, fp)


def build_url(target: str, path: str) -> str:
    if target not in TARGETS:
        raise QueryError(f"unknown target {target[:40]!r}: use one of {', '.join(TARGETS)}")
    base, prefix = TARGETS[target]
    if any(c.isspace() for c in path):
        raise QueryError("the path contains whitespace; encode spaces as %20")
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise QueryError("the path contains a control character")
    for bad in ("..", "@", "\\", "://", "#"):
        if bad in path:
            raise QueryError(f"the path may not contain {bad!r}")
    if not path.startswith(prefix):
        raise QueryError(f"the path must start with {prefix} for {target}")
    endpoint = path.split("?", 1)[0]
    if not _ENDPOINT.fullmatch(endpoint) or endpoint == prefix:
        raise QueryError(f"the endpoint {endpoint[:200]!r} must be a plain path under {prefix}")
    if "admin" in endpoint.split("/"):
        raise QueryError("admin endpoints are refused")
    return (_TEST_BASE or base) + urllib.parse.quote(path, safe=_SAFE)


def _opener():
    # ProxyHandler({}) ignores http_proxy and friends; the redirect handler replaces
    # the default one, so a 3xx is an error instead of a second request.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirect())


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(args) != 2:
        print(USAGE, file=sys.stderr)
        return 1
    try:
        url = build_url(*args)
    except QueryError as e:
        print(f"triage-query: {e}\n{USAGE}", file=sys.stderr)
        return 1
    try:
        with _opener().open(urllib.request.Request(url, method="GET"), timeout=TIMEOUT) as r:
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        detail = b""
        try:
            detail = e.read(2000) if e.fp is not None else b""
        except Exception:
            pass
        sys.stderr.write(f"triage-query: HTTP {e.code}: {e.reason}\n")
        sys.stderr.flush()
        if detail:
            sys.stderr.buffer.write(detail + b"\n")
            sys.stderr.buffer.flush()
        return 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"triage-query: request failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(body[:MAX_BYTES])
    sys.stdout.buffer.flush()
    if len(body) > MAX_BYTES:
        print(f"\ntriage-query: output truncated at {MAX_BYTES} bytes; narrow the query", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
