import pytest

from testsupport import make_policy


def test_parses_policy_and_defaults_to_shadow():
    p = make_policy()
    assert p.model == "claude-opus-5-5"
    assert p.renovate_denylist == ("velero-plugin-for-aws",)
    assert p.is_live("oom-memory-limit") is False


def test_live_class():
    p = make_policy(classes={"oom-memory-limit": "live"})
    assert p.is_live("oom-memory-limit") is True
    assert p.is_live("cpu-throttle-limit") is False   # absent = not live


def test_rejects_unknown_class():
    with pytest.raises(ValueError, match="unknown class"):
        make_policy(classes={"restart-the-pod": "live"})


def test_rejects_bad_mode():
    with pytest.raises(ValueError, match="mode must be shadow or live"):
        make_policy(classes={"oom-memory-limit": "on"})


def test_rejects_deadline_too_short_for_publish():
    with pytest.raises(ValueError, match="job_deadline_seconds"):
        make_policy(job_deadline_seconds=300)
