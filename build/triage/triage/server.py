"""Dispatcher Deployment entrypoint: webhook intake, health, metrics, scheduling loop."""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
import yaml
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import alertmanager, jobs
from .dispatcher import Dispatcher
from .ghapp import CachedToken, GitHub, GitHubApp
from .owners import kube_owner_lookup
from .policy import load_policy
from .pushover import Pushover

log = logging.getLogger("triage.server")
MAX_BODY = 2_000_000
TEMPLATE_PATH = "/etc/triage/job-template.yaml"
TICK_SECONDS = 30
# The dispatcher calls GitHub inside its global lock, which also serializes webhook
# intake; a GitHub Retry-After (up to 60s per retry by default) must not stall it.
DISPATCHER_MAX_RETRY_AFTER = 5


def make_handler(dispatcher, lock):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body=b"", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                return self._reply(200, b"ok")
            if self.path == "/metrics":
                return self._reply(200, generate_latest(), CONTENT_TYPE_LATEST)
            return self._reply(404)

        def do_POST(self):
            header = (self.headers.get("Content-Length") or "0").strip()
            length = int(header) if header.isascii() and header.isdigit() else -1
            if length < 0:
                # Never block on, or drop, a request we cannot frame.
                return self._reply(400, b"invalid Content-Length")
            if length > MAX_BODY:
                return self._reply(413)
            raw = self.rfile.read(length)
            if self.path not in ("/alertmanager", "/sweep"):
                return self._reply(404)
            try:
                payload = json.loads(raw) if self.path == "/alertmanager" else None
            except ValueError as e:          # json.JSONDecodeError, UnicodeDecodeError
                # 4xx: Alertmanager does not retry, and resending the same bytes won't help.
                return self._reply(400, str(e).encode())
            try:
                with lock:
                    if self.path == "/alertmanager":
                        result = dispatcher.handle_alertmanager(payload)
                    else:
                        result = dispatcher.handle_sweep()
            except Exception:
                # Any dispatcher failure is ours, not the sender's: 5xx makes
                # Alertmanager retry the webhook, which is what we want.
                log.exception("handling %s failed", self.path)
                return self._reply(500)
            return self._reply(200, json.dumps(result).encode(), "application/json")

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

    return Handler


def dispatcher_github(reports_repo_name: str) -> GitHub:
    """The dispatcher's GitHub client: Issues-only on the reports repo, short waits."""
    app = GitHubApp.from_dir(max_retry_after=DISPATCHER_MAX_RETRY_AFTER)
    return GitHub(CachedToken(app, [reports_repo_name], {"issues": "write", "metadata": "read"}),
                  max_retry_after=DISPATCHER_MAX_RETRY_AFTER)


def main():
    from kubernetes import client, config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    policy = load_policy()
    with open(TEMPLATE_PATH) as f:
        template = yaml.safe_load(f)
    jobs.check_template(template)
    config.load_incluster_config()
    core, apps, batch = client.CoreV1Api(), client.AppsV1Api(), client.BatchV1Api()
    reports_repo_name = policy.reports_repo.split("/", 1)[1]
    github = dispatcher_github(reports_repo_name)
    pushover = Pushover.from_dir()
    session = requests.Session()
    dispatcher = Dispatcher(
        policy=policy, queue=jobs.JobQueue(batch), template=template, github=github,
        get_owner=kube_owner_lookup(core, apps, batch),
        still_firing=lambda fps: alertmanager.still_firing(fps, session),
        notify=pushover.send,
        read_outcome=lambda name: jobs.read_outcome(core, jobs.NAMESPACE, name),
    )
    lock = threading.Lock()

    def loop():
        while True:
            try:
                with lock:
                    result = dispatcher.tick()
                if result not in ("idle", "busy"):
                    log.info("tick: %s", result)
            except Exception:
                log.exception("tick failed")
            time.sleep(TICK_SECONDS)

    threading.Thread(target=loop, daemon=True).start()
    log.info("listening on :8080 (model=%s, classes=%s)", policy.model, policy.classes)
    ThreadingHTTPServer(("0.0.0.0", 8080), make_handler(dispatcher, lock)).serve_forever()


if __name__ == "__main__":
    main()
