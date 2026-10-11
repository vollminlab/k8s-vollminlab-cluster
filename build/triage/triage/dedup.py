"""Should an event start an investigation? GitHub issues are the only state."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class Action(Enum):
    NEW = "dispatched_new"
    COMMENT = "dispatched_comment"
    INFLIGHT = "deduped_inflight"
    RECENT = "deduped_recent"


@dataclass(frozen=True)
class OpenIssue:
    number: int
    updated_at: datetime


def decide(job_exists: bool, issue, now: datetime, reinvestigate_after: timedelta) -> Action:
    if job_exists:
        return Action.INFLIGHT
    if issue is None:
        return Action.NEW
    if now - issue.updated_at < reinvestigate_after:
        return Action.RECENT
    return Action.COMMENT
