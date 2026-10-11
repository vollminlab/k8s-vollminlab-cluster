from datetime import datetime, timedelta, timezone

from triage.dedup import Action, OpenIssue, decide

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(hours=24)


def test_job_in_flight_wins_over_everything():
    assert decide(True, None, NOW, DAY) is Action.INFLIGHT
    assert decide(True, OpenIssue(5, NOW - timedelta(days=9)), NOW, DAY) is Action.INFLIGHT


def test_no_issue_dispatches_new():
    assert decide(False, None, NOW, DAY) is Action.NEW


def test_recent_issue_drops():
    assert decide(False, OpenIssue(5, NOW - timedelta(hours=1)), NOW, DAY) is Action.RECENT


def test_stale_issue_reinvestigates_as_comment():
    assert decide(False, OpenIssue(5, NOW - timedelta(hours=30)), NOW, DAY) is Action.COMMENT


def test_boundary_is_reinvestigated():
    assert decide(False, OpenIssue(5, NOW - DAY), NOW, DAY) is Action.COMMENT
