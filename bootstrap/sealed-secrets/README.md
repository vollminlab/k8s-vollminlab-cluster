# Sealed Secrets — Bootstrap Reference

> **⚠️ RETIRED (2026-05-31).** SealedSecrets are no longer used in this cluster. The
> sealed-secrets controller has been removed and all secrets are now provided by **ESO +
> 1Password Connect** — see `.claude/rules/secrets.md`. The DR-critical root secret is now the
> `onepassword-connect` Secret (`1password-credentials.json` + `token`) in the `1password`
> namespace, not the sealing key. This document is kept for historical reference only and is
> **not** part of the live disaster-recovery path.

**The sealing key is NOT managed by Flux.** The sealed-secrets controller is deployed via Flux, but the sealing key secret must be backed up and restored manually. If the controller is ever reinstalled without restoring this key first, all existing SealedSecrets become permanently unreadable.

The sealing key is backed up in 1Password as a Secure Note: **"Sealed Secrets Sealing Key"**.

## Exporting the sealing key (backup)

```bash
kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml
```

This outputs a TLS key pair (certificate + private key) as YAML with base64-encoded values. Save the entire output to the 1Password Secure Note.

## Restoring on a new cluster (disaster recovery)

Restore the key **before** bootstrapping Flux so the controller finds it on first start:

```bash
# 1. Apply the exported secret from 1Password
kubectl apply -f <the-exported-yaml>

# 2. Bootstrap Flux — the sealed-secrets controller will find the existing key and use it
```

If Flux is already running and sealed-secrets was reinstalled without the key:

```bash
# 1. Restore the key secret
kubectl apply -f <the-exported-yaml>

# 2. Restart the controller so it picks up the restored key
kubectl rollout restart deployment -n sealed-secrets sealed-secrets
```

## Bootstrap order

```
1. Install Kubernetes control plane
2. Install Calico CNI (see ../calico/README.md)
3. Restore sealing key secret (this step — required before sealed-secrets starts)
4. Bootstrap Flux (deploys sealed-secrets controller via HelmRelease)
```
