"""The image installs `triage` into site-packages and runs every entrypoint as
`python -I -m triage.<module>`; these guard the two ways that can silently break."""
import fnmatch
import re
import tomllib
from pathlib import Path

HERE = Path(__file__).parent
PYPROJECT = tomllib.loads((HERE / "pyproject.toml").read_text())


def test_every_data_file_the_package_reads_is_installed():
    # A file missing from package-data exists in the source tree (so every other test
    # passes) but not in the image, where it would fail only at runtime.
    patterns = PYPROJECT["tool"]["setuptools"]["package-data"]["triage"]
    pkg = HERE / "triage"
    data = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
            if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts]
    assert data
    for rel in data:
        assert any(fnmatch.fnmatch(rel, pat) for pat in patterns), rel


def test_the_image_runs_python_isolated():
    docker = (HERE / "Dockerfile").read_text()
    cmd = re.search(r"^CMD (.+)$", docker, re.M).group(1)
    assert cmd.startswith('["python", "-I"'), cmd
    assert "pip install --no-cache-dir --no-deps /tmp/src" in docker
    assert "COPY triage/ ./triage/" not in docker        # no importable copy left in a workdir


def test_triage_query_console_script_runs_isolated_in_the_image():
    # pip writes the console script with a bare `#!/usr/local/bin/python` shebang; the
    # image appends -I to it, like every other entrypoint, and checks that it ran.
    docker = (HERE / "Dockerfile").read_text()
    assert "sed -i '1s|^#!\\(.*python[0-9.]*\\)$|#!\\1 -I|' /usr/local/bin/triage-query" in docker
    assert 'head -1 /usr/local/bin/triage-query | grep -q " -I$"' in docker
    assert "triage-query --help" in docker
