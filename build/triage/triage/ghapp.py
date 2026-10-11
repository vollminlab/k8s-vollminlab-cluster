"""GitHub App authentication and the handful of REST calls triage needs."""
import time

import jwt
import requests

API = "https://api.github.com"
SECRET_DIR = "/var/run/secrets/triage/github"
MAX_RETRY_AFTER = 60


def _check_retries(retries: int) -> int:
    if retries < 1:
        raise ValueError(f"retries must be >= 1, got {retries}")
    return retries


def _check_max_retry_after(seconds) -> int:
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        raise ValueError(f"max_retry_after must be an int >= 0, got {seconds!r}")
    return seconds


def _retry_after(r):
    """The response's Retry-After in whole seconds, or None if absent or not delta-seconds."""
    after = str((r.headers or {}).get("Retry-After", "")).strip()
    return int(after) if after.isascii() and after.isdigit() else None


def _backoff(r, attempt: int, max_retry_after: int = MAX_RETRY_AFTER) -> float:
    """Retry-After on 403/429/503, else exponential; never longer than max_retry_after."""
    if r is not None and r.status_code in (403, 429, 503):
        after = _retry_after(r)
        if after is not None:
            return min(after, max_retry_after)
    return min(2 ** attempt, max_retry_after)


def _secondary_rate_limit(r) -> bool:
    # GitHub's secondary rate limit answers 403 (not 429) with a Retry-After header.
    # A plain 403 is a permission error and is never retried.
    return r.status_code == 403 and _retry_after(r) is not None


def send_with_retries(send, retries: int, sleep, retry_unsafe: bool = True,
                      max_retry_after: int = MAX_RETRY_AFTER):
    """Call `send()` until it returns a non-retryable response, and return it.

    Retried: 429 always; 5xx, timeouts, connection errors and a secondary-rate-limit
    403 (one carrying Retry-After) only when `retry_unsafe` is true. A non-idempotent
    create passes False, because GitHub can fail (502, dropped connection) AFTER
    creating the object, and a re-POST would duplicate it; 429 is a refusal before
    processing, so it is always safe. Each wait is capped at `max_retry_after`
    seconds, which a caller holding a lock sets low.
    """
    for attempt in range(retries):
        last = attempt == retries - 1
        try:
            r = send()
        except (requests.ConnectionError, requests.Timeout):
            if last or not retry_unsafe:
                raise
            sleep(_backoff(None, attempt, max_retry_after))
            continue
        retryable = r.status_code == 429 or (retry_unsafe and (r.status_code >= 500 or _secondary_rate_limit(r)))
        if retryable and not last:
            sleep(_backoff(r, attempt, max_retry_after))
            continue
        return r


class GitHubApp:
    def __init__(self, app_id: str, installation_id: str, private_key_pem: str, session=None,
                 retries: int = 3, sleep=time.sleep, max_retry_after: int = MAX_RETRY_AFTER):
        self.app_id = str(app_id)
        self.installation_id = str(installation_id)
        self.private_key = private_key_pem
        self.session = session or requests.Session()
        self.retries = _check_retries(retries)
        self.sleep = sleep
        self.max_retry_after = _check_max_retry_after(max_retry_after)

    @classmethod
    def from_dir(cls, path: str = SECRET_DIR, session=None, **kwargs) -> "GitHubApp":
        def read(name):
            with open(f"{path}/{name}") as f:
                return f.read().strip()
        return cls(read("app_id"), read("installation_id"), read("private_key"), session, **kwargs)

    def _jwt(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                          self.private_key, algorithm="RS256")

    def token(self, repositories: list, permissions: dict) -> str:
        """An installation token scoped DOWN to these repos and permissions.

        The App itself can write to the public cluster repo; a token minted here
        for the dispatcher carries only issues:write on triage-reports.
        """
        # Minting is idempotent (each call just issues a fresh token), so every
        # transient failure is retried; the JWT is re-signed per attempt.
        r = send_with_retries(lambda: self.session.post(
            f"{API}/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._jwt()}", "Accept": "application/vnd.github+json"},
            json={"repositories": repositories, "permissions": permissions},
            timeout=30,
        ), self.retries, self.sleep, max_retry_after=self.max_retry_after)
        r.raise_for_status()
        return r.json()["token"]


class CachedToken:
    """Installation tokens live 60 minutes; re-mint after `ttl` seconds."""

    def __init__(self, app, repositories, permissions, ttl: int = 3000, clock=time.time):
        self.app, self.repositories, self.permissions = app, repositories, permissions
        self.ttl, self.clock = ttl, clock
        self._token, self._at = None, 0.0

    def __call__(self) -> str:
        if self._token is None or self.clock() - self._at > self.ttl:
            self._token = self.app.token(self.repositories, self.permissions)
            self._at = self.clock()
        return self._token


class GitHub:
    def __init__(self, token_provider, session=None, retries: int = 3, sleep=time.sleep,
                 max_retry_after: int = MAX_RETRY_AFTER):
        self.token_provider = token_provider
        self.session = session or requests.Session()
        self.retries = _check_retries(retries)
        self.sleep = sleep
        self.max_retry_after = _check_max_retry_after(max_retry_after)

    def _request(self, method, path, retry_unsafe: bool = True, **kwargs):
        def send():
            headers = {"Authorization": f"Bearer {self.token_provider()}",
                       "Accept": "application/vnd.github+json"}
            return self.session.request(method, f"{API}{path}", headers=headers, timeout=30, **kwargs)
        r = send_with_retries(send, self.retries, self.sleep, retry_unsafe, self.max_retry_after)
        r.raise_for_status()
        return r.json() if r.content else None

    def find_open_issue(self, repo: str, fp12: str):
        items = self._request("GET", f"/repos/{repo}/issues",
                              params={"labels": f"fp/{fp12}", "state": "open", "per_page": 5})
        return items[0] if items else None

    def create_issue(self, repo, title, body, labels):
        return self._request("POST", f"/repos/{repo}/issues", retry_unsafe=False,
                             json={"title": title, "body": body, "labels": labels})

    def comment(self, repo, number, body):
        return self._request("POST", f"/repos/{repo}/issues/{number}/comments", retry_unsafe=False,
                             json={"body": body})

    def add_labels(self, repo, number, labels):
        return self._request("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": labels})

    def close_issue(self, repo, number):
        return self._request("PATCH", f"/repos/{repo}/issues/{number}", json={"state": "closed"})

    def create_pull(self, repo, title, head, base, body):
        return self._request("POST", f"/repos/{repo}/pulls", retry_unsafe=False,
                             json={"title": title, "head": head, "base": base, "body": body})
