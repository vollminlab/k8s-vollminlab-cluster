FROM python:3.14-slim

# Pinned to match the cluster (kubectl, flux) and CI (kustomize, kyverno, gitleaks),
# so gate 4 validates exactly as the PR's own CI run will.
ARG KUBECTL_VERSION=1.34.8
ARG FLUX_VERSION=2.8.5
ARG KUSTOMIZE_VERSION=5.4.3
ARG KUBECONFORM_VERSION=0.8.0
ARG KYVERNO_VERSION=1.12.1
ARG GITLEAKS_VERSION=8.30.1
ARG CLAUDE_CODE_VERSION=2.1.283

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
  curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl"; \
  chmod 0755 /usr/local/bin/kubectl; \
  curl -fsSL "https://github.com/fluxcd/flux2/releases/download/v${FLUX_VERSION}/flux_${FLUX_VERSION}_linux_amd64.tar.gz" | tar xz -C /usr/local/bin flux; \
  curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" | tar xz -C /usr/local/bin kustomize; \
  curl -fsSL "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" | tar xz -C /usr/local/bin kubeconform; \
  curl -fsSL "https://github.com/kyverno/kyverno/releases/download/v${KYVERNO_VERSION}/kyverno-cli_v${KYVERNO_VERSION}_linux_x86_64.tar.gz" | tar xz -C /usr/local/bin kyverno; \
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" | tar xz -C /usr/local/bin gitleaks; \
  kubectl version --client; flux version --client; kustomize version; kubeconform -v; kyverno version; gitleaks version; \
  gitleaks dir --help > /dev/null

# Native installer, then copy the single binary out of root's home so the
# non-root runtime user can execute it.
RUN set -eux; \
  curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"; \
  cp -L /root/.local/bin/claude /usr/local/bin/claude; \
  rm -rf /root/.local /root/.claude /root/.claude.json; \
  claude --version | grep -F "${CLAUDE_CODE_VERSION}"

ENV DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

COPY requirements.txt /tmp/src/
RUN pip install --no-cache-dir -r /tmp/src/requirements.txt
# Installed into site-packages, with no copy left in a working directory: every
# entrypoint runs `python -I -m triage.<module>` (isolated mode: no cwd, no
# PYTHONPATH/PYTHON* env and no user site-packages on sys.path), so a .pth file or
# module planted under a writable HOME or cwd can never load. See pyproject.toml.
COPY pyproject.toml /tmp/src/
COPY triage/ /tmp/src/triage/
# triage-query (the agent's VM/Loki client, a [project.scripts] console script) gets
# a bare python shebang from pip; append -I so it runs isolated too.
RUN pip install --no-cache-dir --no-deps /tmp/src \
 && rm -rf /tmp/src \
 && cd / && python -I -c "import triage.publish, triage.investigate, triage.server, triage.sweep_tick, triage.result, triage.query" \
 && sed -i '1s|^#!\(.*python[0-9.]*\)$|#!\1 -I|' /usr/local/bin/triage-query \
 && head -1 /usr/local/bin/triage-query | grep -q " -I$" \
 && triage-query --help > /dev/null
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY --chmod=0755 triage-api-key /usr/local/bin/triage-api-key

# The real binary must accept the schema investigate passes to --json-schema. 0.1.0
# shipped a draft-2020-12 `$schema` that claude rejected before any API call, so every
# investigation failed `invalid_output`, and no unit test could see it: CI runs them in a
# plain python image without claude. With no key mounted, apiKeyHelper fails and claude
# still prints its JSON result line, so this needs no network and spends nothing.
RUN cd /tmp \
 && out=$(timeout 120 claude -p x --max-turns 1 --output-format json --json-schema \
      "$(python -I -c 'import json; from triage.result import relaxed_schema; print(json.dumps(relaxed_schema()))')" 2>&1 || true) \
 && rm -rf /tmp/.claude /tmp/.claude.json /tmp/claude-0 \
 && if printf '%s' "$out" | grep -q 'not a valid JSON Schema' || ! printf '%s' "$out" | grep -q '"session_id"'; then \
      printf '%s\n' "$out" | head -c 2000; echo; echo 'claude rejected the --json-schema investigate sends'; exit 1; fi

USER 65532:65532
WORKDIR /
CMD ["python", "-I", "-u", "-m", "triage.server"]
