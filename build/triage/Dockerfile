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

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY triage/ ./triage/
COPY managed-settings.json /etc/claude-code/managed-settings.json
COPY --chmod=0755 triage-api-key /usr/local/bin/triage-api-key

USER 65532:65532
CMD ["python", "-m", "triage.server"]
