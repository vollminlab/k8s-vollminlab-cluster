#!/bin/sh
# Warn BEFORE the vCenter metrics account's password expires.
#
# vCenter does not expose password age: `govc sso.user.ls -json` returns only
# Disabled/Locked/Details/groups, and the SSO API has no last-set field. So expiry
# has to be derived from the rotation date plus the SSO Local Password Policy
# (`govc sso.lpp.info` -> PasswordLifetimeDays, 90 as of 2026-08-18).
#
# Deliberately takes NO credentials -- it is pure date arithmetic. It must never
# be a reason for vCenter or 1Password access to exist inside the cluster.
#
# On "expiring soon" it exits non-zero so the existing KubeJobFailed alert fires.
# That reuses alerting we already have instead of adding an exporter or a rule.
set -eu

: "${ACCOUNT:?ACCOUNT not set}"
: "${ROTATED_AT:?ROTATED_AT not set (YYYY-MM-DD)}"
: "${LIFETIME_DAYS:?LIFETIME_DAYS not set}"
: "${WARN_DAYS:?WARN_DAYS not set}"

# busybox needs -D for an explicit input format; GNU date rejects -D and parses
# ISO dates natively. Try busybox first, fall back to GNU, so the same script runs
# in the alpine image and under a developer's shell.
to_epoch() {
  date -u -D '%Y-%m-%d' -d "$1" +%s 2>/dev/null \
    || date -u -d "$1" +%s 2>/dev/null
}

rot_epoch=$(to_epoch "$ROTATED_AT") || rot_epoch=""
if [ -z "$rot_epoch" ]; then
  echo "ERROR: could not parse ROTATED_AT='$ROTATED_AT' (want YYYY-MM-DD)"
  exit 2
fi

# NOW_EPOCH is a test seam; unset in production.
now_epoch=${NOW_EPOCH:-$(date -u +%s)}
exp_epoch=$((rot_epoch + LIFETIME_DAYS * 86400))
days_left=$(((exp_epoch - now_epoch) / 86400))
exp_date=$(date -u -d "@${exp_epoch}" +%Y-%m-%d 2>/dev/null || echo "epoch ${exp_epoch}")

echo "account=${ACCOUNT} rotated=${ROTATED_AT} lifetime=${LIFETIME_DAYS}d expires=${exp_date} days_left=${days_left}"

if [ "$days_left" -gt "$WARN_DAYS" ]; then
  echo "OK: ${days_left} days remaining (warn at ${WARN_DAYS})"
  exit 0
fi

if [ "$days_left" -lt 0 ]; then
  echo "EXPIRED ${days_left#-} days ago -- the exporter is probably already failing."
else
  echo "EXPIRING in ${days_left} days (warn threshold ${WARN_DAYS})."
fi

cat <<'MSG'

Rotate it (see docs/runbooks/vcenter-metrics-credential-rotation.md):
  1. govc sso.user.update -p <new> prometheus-exporter     # the ONLY vSphere write
  2. update 1Password item "vCenter Metrics" (field: password)
  3. kubectl annotate externalsecret -n monitoring vmware-exporter-credentials \
       force-sync="$(date +%s)" --overwrite                # ESO refreshInterval is 24h --
                                                           # without this the old password
                                                           # is re-rendered by Helm
  4. flux reconcile helmrelease vmware-exporter -n monitoring --with-source
  5. bump ROTATED_AT in the vcenter-credential-age CronJob and merge

This job failing IS the alert. It takes no credentials and cannot rotate anything itself.
MSG
exit 1
