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
        try:
            findings = json.loads(report.read_text() or "[]")
        except ValueError as e:
            raise LeakScanError(f"gitleaks report is not JSON: {e}") from None
    # A report we cannot read is a failed scan, never "no findings": an unexpected
    # shape must stop the publish, not let unscanned text through.
    if not isinstance(findings, list):
        raise LeakScanError(f"gitleaks report is a {type(findings).__name__}, not a list")
    hits = {}
    for f in findings:
        if not (isinstance(f, dict) and isinstance(f.get("File"), str) and isinstance(f.get("Secret"), str)):
            raise LeakScanError("gitleaks report has an entry without string File and Secret")
        hits.setdefault(Path(f["File"]).stem, []).append(f["Secret"])
    return hits


def redact(text: str, secrets) -> str:
    for s in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(s, "[REDACTED]")
    return text
