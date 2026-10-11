"""Pushover notifications. Same API the kubeadm-cert-monitor CronJob uses."""
import requests

URL = "https://api.pushover.net/1/messages.json"
SECRET_DIR = "/var/run/secrets/triage/pushover"


class Pushover:
    def __init__(self, token: str, user: str, session=None):
        self.token, self.user = token, user
        self.session = session or requests

    @classmethod
    def from_dir(cls, path: str = SECRET_DIR, session=None) -> "Pushover":
        def read(name):
            with open(f"{path}/{name}") as f:
                return f.read().strip()
        return cls(read("token"), read("user-key"), session)

    def send(self, title: str, message: str, url: str = None):
        # Pushover rejects an empty message and truncates past these lengths anyway.
        data = {"token": self.token, "user": self.user,
                "title": title[:250], "message": (message or "(no detail)")[:1024]}
        if url:
            data["url"] = url
        r = self.session.post(URL, data=data, timeout=15)
        r.raise_for_status()
