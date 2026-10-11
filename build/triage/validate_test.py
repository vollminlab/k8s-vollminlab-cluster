import subprocess
from pathlib import Path

from triage.validate import kyverno_policies, validate

POLICY = "apiVersion: kyverno.io/v1\nkind: ClusterPolicy\nmetadata: {name: p}\n"


def repo_with_policies(tmp_path, *extra):
    d = tmp_path / "clusters/vollminlab-cluster/kyverno/kyverno-policies/policies"
    d.mkdir(parents=True)
    (d / "ok.yaml").write_text(POLICY)
    (d / "api.yaml").write_text(POLICY + "spec:\n  rules: [{context: [{apiCall: {}}]}]\n")
    (d / "kustomization.yaml").write_text("resources: [ok.yaml]\n")
    for name, text in extra:
        (d / name).write_text(text)
    return tmp_path


class Runner:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = 1 if cmd[0] == self.fail else 0
        return subprocess.CompletedProcess(cmd, rc, "out", "err" if rc else "")


def test_policy_selection_mirrors_ci(tmp_path):
    names = [p.rsplit("/", 1)[1] for p in kyverno_policies(repo_with_policies(tmp_path))]
    assert names == ["ok.yaml"]


def test_all_three_steps_run_in_order(tmp_path):
    r = Runner()
    assert validate(repo_with_policies(tmp_path), "clusters/vollminlab-cluster/demo/demo/app/", r) is None
    assert [c[0] for c in r.calls] == ["kustomize", "kubeconform", "kyverno"]
    assert "-strict" in r.calls[1] and "--resource" in r.calls[2]


def test_a_failing_step_is_reported(tmp_path):
    msg = validate(repo_with_policies(tmp_path), "x/", Runner(fail="kubeconform"))
    assert msg.startswith("kubeconform failed")


def test_no_policies_is_an_error_not_a_pass(tmp_path):
    msg = validate(tmp_path, "x/", Runner())
    assert "no Kyverno policies" in msg


def test_renders_outside_the_repo_and_leaves_nothing_behind(tmp_path):
    # publish refuses a checkout with untracked or ignored files, so validation
    # must not write into it: render into a temporary directory instead.
    repo = repo_with_policies(tmp_path / "repo")
    seen = []

    def run(cmd, **kw):
        if cmd[0] == "kustomize":
            out = Path(cmd[cmd.index("-o") + 1])
            out.write_text("kind: ConfigMap\n")
            seen.append(out)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    before = sorted(p for p in repo.rglob("*"))
    assert validate(repo, "clusters/vollminlab-cluster/demo/demo/app/", run) is None
    assert not seen[0].is_relative_to(repo)
    assert not seen[0].exists()                       # the temporary directory is gone
    assert sorted(p for p in repo.rglob("*")) == before
