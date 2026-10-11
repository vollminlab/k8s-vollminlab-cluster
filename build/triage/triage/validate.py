"""Gate 4: kustomize build, kubeconform and kyverno apply, as CI runs them."""
import subprocess
import tempfile
from pathlib import Path

CATALOG = ("https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
           "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")


def kyverno_policies(repo: Path) -> list:
    out = []
    for p in sorted(repo.glob("clusters/**/kyverno/**/policies/**/*.y*ml")):
        text = p.read_text()
        if "kind: ClusterPolicy" not in text:
            continue
        # .github/workflows/ci.yaml skips these too: the CLI cannot evaluate them offline.
        if "apiCall:" in text or "mutateExistingOnPolicyUpdate:" in text:
            continue
        out.append(str(p))
    return out


def validate(repo: Path, app_dir: str, run=subprocess.run):
    """None when everything passed, otherwise a description of the first failure."""
    policies = kyverno_policies(repo)
    if not policies:
        # An empty policy list would make `kyverno apply` vacuous. Refuse rather
        # than report a validation that checked nothing.
        return "no Kyverno policies found under clusters/**/kyverno/**/policies; refusing to pass"
    # Rendered outside the checkout: publish refuses to commit from a checkout that
    # has any untracked or ignored file, so validation must leave it untouched.
    with tempfile.TemporaryDirectory(prefix="triage-validate-") as d:
        return _run_steps(repo, app_dir, policies, Path(d) / "rendered.yaml", run)


def _run_steps(repo: Path, app_dir: str, policies: list, rendered: Path, run):
    steps = [
        ("kustomize build", ["kustomize", "build", str(repo / app_dir), "-o", str(rendered)]),
        ("kubeconform", ["kubeconform", "-strict", "-ignore-missing-schemas",
                         "-schema-location", "default", "-schema-location", CATALOG,
                         "-summary", str(rendered)]),
        ("kyverno apply", ["kyverno", "apply", *policies, "--resource", str(rendered)]),
    ]
    for name, cmd in steps:
        try:
            cp = run(cmd, capture_output=True, text=True, timeout=300)
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"{name}: {e}"
        if cp.returncode != 0:
            return f"{name} failed: {(cp.stdout + cp.stderr)[-2000:]}"
    return None
