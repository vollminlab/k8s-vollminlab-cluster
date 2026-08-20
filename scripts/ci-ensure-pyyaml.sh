#!/usr/bin/env bash
# Make `import yaml` work for the Python helpers CI runs.
#
# The self-hosted runner image ships python3 with NO pip and NO ensurepip, so a
# bare `python3 -m pip install pyyaml` dies with "No module named pip". The
# yamllint step in ci.yaml has bootstrapped get-pip.py for this reason since it
# was written; two later steps (check-helm-values.py, check-tofu-provider-approval.py)
# did not, and both were broken -- one visibly, one silently, because its paths
# filter had never matched a PR. This script is the shared, correct form.
#
# Bootstrap only if the import is genuinely missing, so a runner image that
# gains python3-yaml costs nothing.
set -euo pipefail

# PY is overridable ONLY so the no-pip bootstrap branch can be tested against a
# venv built --without-pip, which reproduces the runner image exactly. CI never sets it.
PY="${PY:-python3}"

if "$PY" -c 'import yaml' 2>/dev/null; then
  echo "✅ pyyaml already available"
  exit 0
fi

echo "📦 pyyaml missing — bootstrapping pip"

# --user is correct on the runner (no venv, PEP 668 marks the system env external)
# but is an error inside a venv, where user site-packages are not visible. Detect
# rather than assume, so this behaves the same in CI and under test.
if "$PY" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'; then
  INSTALL_FLAGS=""
else
  INSTALL_FLAGS="--user --break-system-packages"
fi
# timeouts so a hung download fails fast instead of stalling the job to its
# own timeout-minutes.
timeout 90 curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
timeout 90 "$PY" /tmp/get-pip.py $INSTALL_FLAGS --quiet
timeout 90 "$PY" -m pip install $INSTALL_FLAGS --quiet pyyaml

# Verify the artifact, not the exit code: a --quiet install that resolved to
# nothing still exits 0. The import is the only proof that matters, and failing
# here is strictly better than letting a dependent check pass vacuously.
"$PY" -c 'import yaml; print(f"✅ pyyaml {yaml.__version__} ready")'
