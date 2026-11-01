"""The triage-policy ConfigMap: models, caps, and which fix classes may open PRs."""
from dataclasses import dataclass

import yaml

KNOWN_CLASSES = ("oom-memory-limit", "cpu-throttle-limit", "renovate-breakage")
MODES = ("shadow", "live")
DEFAULT_PATH = "/etc/triage/policy.yaml"


@dataclass(frozen=True)
class Policy:
    model: str
    max_turns: int
    per_run_budget_usd: float
    daily_budget_usd: float
    queue_cap: int
    reinvestigate_after_hours: int
    recur_window_hours: int
    job_deadline_seconds: int
    reports_repo: str
    cluster_repo: str
    classes: dict
    renovate_denylist: tuple
    sweep_checklist: str

    def is_live(self, cls: str) -> bool:
        return self.classes.get(cls) == "live"


def parse_policy(raw: dict) -> Policy:
    classes = dict(raw["classes"])
    for name, mode in classes.items():
        if name not in KNOWN_CLASSES:
            raise ValueError(f"unknown class {name!r}; known: {', '.join(KNOWN_CLASSES)}")
        if mode not in MODES:
            raise ValueError(f"class {name!r}: mode must be shadow or live, got {mode!r}")
    deadline = int(raw["job_deadline_seconds"])
    if deadline < 600:
        # investigate.py reserves 300s for publish and 120s for pod startup, and will
        # not start claude with 60s or less left.
        raise ValueError(f"job_deadline_seconds must be at least 600, got {deadline}")
    return Policy(
        model=str(raw["model"]),
        max_turns=int(raw["max_turns"]),
        per_run_budget_usd=float(raw["per_run_budget_usd"]),
        daily_budget_usd=float(raw["daily_budget_usd"]),
        queue_cap=int(raw["queue_cap"]),
        reinvestigate_after_hours=int(raw["reinvestigate_after_hours"]),
        # A closed issue recurring within this window is reopened instead of re-filed.
        recur_window_hours=int(raw.get("recur_window_hours", 168)),
        job_deadline_seconds=deadline,
        reports_repo=str(raw["reports_repo"]),
        cluster_repo=str(raw["cluster_repo"]),
        classes=classes,
        renovate_denylist=tuple(raw.get("renovate_denylist", [])),
        sweep_checklist=str(raw["sweep_checklist"]),
    )


def load_policy(path: str = DEFAULT_PATH) -> Policy:
    with open(path) as f:
        return parse_policy(yaml.safe_load(f))
