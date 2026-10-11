import pytest

from testsupport import make_patch
from triage.patching import PatchError, apply, parse

OLD = "a: 1\nb: 2\nc: 3\nd: 4\ne: 5\nf: 6\ng: 7\nh: 8\ni: 9\nj: 10\n"


def test_round_trip_single_hunk():
    new = OLD.replace("b: 2", "b: 20")
    [fp] = parse(make_patch("x.yaml", OLD, new))
    assert (fp.old_path, fp.new_path) == ("x.yaml", "x.yaml")
    assert apply(OLD, fp) == new


def test_two_hunks():
    new = OLD.replace("a: 1", "a: 0").replace("j: 10", "j: 11")
    [fp] = parse(make_patch("x.yaml", OLD, new))
    assert len(fp.hunks) == 2
    assert apply(OLD, fp) == new


def test_context_mismatch_is_rejected():
    [fp] = parse(make_patch("x.yaml", OLD, OLD.replace("b: 2", "b: 20")))
    with pytest.raises(PatchError, match="mismatch"):
        apply(OLD.replace("a: 1", "a: 99"), fp)


def test_dev_null_means_add_or_delete():
    [fp] = parse("--- /dev/null\n+++ b/new.yaml\n@@ -0,0 +1 @@\n+x: 1\n")
    assert fp.old_path is None and fp.new_path == "new.yaml"


def test_no_newline_marker_is_ignored_and_trailing_newline_kept():
    text = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a: 1\n\\ No newline at end of file\n+a: 2\n\\ No newline at end of file\n"
    [fp] = parse(text)
    assert apply("a: 1", fp) == "a: 2"


def test_garbage_is_rejected():
    with pytest.raises(PatchError):
        parse("this is not a diff")
    with pytest.raises(PatchError):
        parse("--- a/x\n@@ -1 +1 @@\n")
    with pytest.raises(PatchError):
        parse("--- a/x\n+++ b/x\n@@ -1 +1 @@\n?weird\n")
