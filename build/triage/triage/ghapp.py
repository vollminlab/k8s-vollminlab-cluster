"""GitHub App authentication and the handful of REST calls triage needs."""
import time

import jwt
import requests

API = "https://api.github.com"
SECRET_DIR = "/var/run/secrets/triage/github"


class GitHubApp:
    def __init__(self, app_id: str, installation_id: str, private_key_pem: str, session=None):
        self.app_id = str(app_id)
        self.installation_id = str(installation_id)
        self.private_key = private_key_pem
        self.session = session or requests.Session()

    @classmethod
    def from_dir(cls, path: str = SECRET_DIR, session=None) -> "GitHubApp":
        def read(name):
            with open(f"{path}/{name}") as f:
                return f.read().strip()
        return cls(read("app_id"), read("installation_id"), read("private_key"), session)

    def _jwt(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                          self.private_key, algorithm="RS256")

    def token(self, repositories: list, permissions: dict) -> str:
        """An installation token scoped DOWN to these repos and permissions.

        The App itself can write to the public cluster repo; a token minted here
        for the dispatcher carries only issues:write on triage-reports.
        """
        r = self.session.post(
            f"{API}/app/installations/{self.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._jwt()}", "Accept": "application/vnd.github+json"},
            json={"repositories": repositories, "permissions": permissions},
            timeout=30,
        )
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
    def __init__(self, token_provider, session=None, retries: int = 3, sleep=time.sleep):
        self.token_provider = token_provider
        self.session = session or requests.Session()
        self.retries = retries
        self.sleep = sleep

    def _request(self, method, path, **kwargs):
        last = None
        for attempt in range(self.retries):
            headers = {"Authorization": f"Bearer {self.token_provider()}",
                       "Accept": "application/vnd.github+json"}
            r = self.session.request(method, f"{API}{path}", headers=headers, timeout=30, **kwargs)
            if r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
                return r.json() if r.content else None
            last = r
            if attempt < self.retries - 1:
                self.sleep(2 ** attempt)
        last.raise_for_status()

    def find_open_issue(self, repo: str, fp12: str):
        items = self._request("GET", f"/repos/{repo}/issues",
                              params={"labels": f"fp/{fp12}", "state": "open", "per_page": 5})
        return items[0] if items else None

    def create_issue(self, repo, title, body, labels):
        return self._request("POST", f"/repos/{repo}/issues",
                             json={"title": title, "body": body, "labels": labels})

    def comment(self, repo, number, body):
        return self._request("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})

    def add_labels(self, repo, number, labels):
        return self._request("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": labels})

    def close_issue(self, repo, number):
        return self._request("PATCH", f"/repos/{repo}/issues/{number}", json={"state": "closed"})

    def create_pull(self, repo, title, head, base, body):
        return self._request("POST", f"/repos/{repo}/pulls",
                             json={"title": title, "head": head, "base": base, "body": body})
