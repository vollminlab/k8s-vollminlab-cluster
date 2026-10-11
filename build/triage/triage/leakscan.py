"""gitleaks over text before it is posted anywhere."""
import json
import subprocess
import tempfile
from pathlib import Path


class LeakScanError(Exception):
    pass


def scan(texts: dict, run=subprocess.run) -> dict:
    """{name: [secret, ...]} for every text with a finding. Names must be filename-safe."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "in"
        root.mkdir()
        for name, text in texts.items():
            (root / f"{name}.txt").write_text(text or "")
        report = Path(d) / "report.json"
        cp = run(["gitleaks", "dir", str(root), "--report-format", "json", "--report-path", str(report),
                  "--exit-code", "0", "--no-banner"], capture_output=True, text=True, timeout=120)
        if cp.returncode != 0 or not report.exists():
            raise LeakScanError(f"gitleaks exited {cp.returncode}: {cp.stderr[-500:]}")
        findings = json.loads(report.read_text() or "[]")
    hits = {}
    for f in findings:
        hits.setdefault(Path(f["File"]).stem, []).append(f["Secret"])
    return hits


def redact(text: str, secrets) -> str:
    for s in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(s, "[REDACTED]")
    return text
