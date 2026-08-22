#!/bin/sh
# dns-aaaa-check: detect a silent split-horizon DNS leak.
#
# Split-horizon DNS on this cluster is A-only: Pi-hole holds a local A record
# for every Ingress host, pointing at the ingress VIP, so LAN clients reach
# nginx directly. AAAA is left to forward upstream. For the handful of hosts
# that also have a Cloudflare tunnel CNAME, upstream returns Cloudflare's
# proxied IPv6 -- so an IPv6-capable LAN client hairpins out through the
# tunnel instead of hitting nginx directly. That adds a needless dependency
# on the tunnel from the LAN and puts Cloudflare's 100MB request-body limit
# in the path of LAN uploads that should never see it.
#
# The fix (append `local=/<host>/` to misc.dnsmasq_lines on pihole1) is hand-
# maintained -- nothing creates it automatically when a new tunnel-exposed
# Ingress appears, unlike the A record which external-dns creates for free.
# This has recurred 5 times and is invisible from the LAN (browsers work
# fine either way), so it is only observable from outside -- exactly how a
# previous instance of this class of bug went unnoticed for 68 days. This
# script makes it loud instead: list every Ingress host, ask Pi-hole for
# AAAA, and fail loudly if anything answers. It does not touch Pi-hole.
set -u

PIHOLE_ADDR="${PIHOLE_ADDR:-192.168.100.4}"
# Any name that is guaranteed to have a real AAAA record. Queried before the
# host loop as a canary -- see canary_check() for why.
CANARY_HOST="${CANARY_HOST:-cloudflare.com}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# list_hosts: every distinct, non-empty Ingress host in the cluster, one per
# line. Returns non-zero (and leaves nothing useful on stdout) if the kubectl
# call itself fails -- that must NOT be reported as a clean pass.
list_hosts() {
  raw=$(kubectl get ingress -A -o jsonpath='{range .items[*]}{range .spec.rules[*]}{.host}{"\n"}{end}{end}' 2>&1)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "$raw" >&2
    return 1
  fi
  printf '%s\n' "$raw" | sed '/^$/d' | sort -u
}

# aaaa_for_host: AAAA addresses nslookup returns for $1 against $PIHOLE_ADDR,
# one per line, on stdout. Empty output means no leak -- but the caller MUST
# also check the exit status ($?), not just whether stdout is empty.
#
# nslookup exits 0 whether it finds an AAAA record, finds nothing, or hits
# NXDOMAIN -- measured against the live Pi-hole 2026-08-22 -- and exits
# non-zero ONLY when the resolver itself could not be reached. So rc is a
# reliable resolver-failure signal, but only if it is captured directly from
# nslookup: reading $? after the pipe below would instead get awk/grep's
# status (0 if a line matched, 1 if not), which can never distinguish "no
# AAAA" from "no answer at all". That is why raw output is captured first,
# with rc read immediately, before it is ever piped anywhere.
#
# `tail -n +3` skips the two header lines that name the server itself (whose
# own "Address:" line is "<server>:53" and would otherwise be misread as a
# leak).
aaaa_for_host() {
  raw=$(nslookup -type=aaaa "$1" "$PIHOLE_ADDR" 2>/dev/null)
  rc=$?
  [ "$rc" -eq 0 ] || return "$rc"
  printf '%s\n' "$raw" | tail -n +3 | awk '/^Address:/{print $2}' | grep -E '^[0-9a-fA-F:]+$'
  # Explicit return 0, not left implicit: without it, the function's own exit
  # status would be grep's -- 1 whenever nothing matched, i.e. on every
  # genuinely clean host -- which would make a clean result indistinguishable
  # from a resolver failure to any caller checking $?. That is the exact bug
  # this function exists to prevent, just one level further down.
  return 0
}

# canary_check: confirm the resolver can produce an AAAA for a name that
# definitely has one, before trusting a "no AAAA" result for anything else.
#
# Without this, a Pi-hole that is down or unreachable looks EXACTLY like a
# clean cluster: nslookup's timeout/no-answer output never matches
# "^Address:", so aaaa_for_host() returns empty either way, and the run would
# report "OK: none leaking" -- a false all-clear produced by this monitoring
# job's own dependency failing, which is precisely the class of silent-pass
# this job exists to catch. Checking this first also fails fast: an
# unreachable resolver costs nslookup ~10s per query in retries, so finding
# out up front avoids burning ~5 minutes discovering it one host at a time
# across ~30 hosts.
canary_check() {
  addrs=$(aaaa_for_host "$CANARY_HOST")
  rc=$?
  if [ "$rc" -ne 0 ]; then
    log "FAIL: canary lookup for $CANARY_HOST via $PIHOLE_ADDR failed (nslookup rc=$rc) -- resolver is unreachable, refusing to trust any 'no AAAA' result from it"
    return 1
  fi
  if [ -z "$addrs" ]; then
    log "FAIL: canary $CANARY_HOST returned no AAAA via $PIHOLE_ADDR, but it has one -- resolver answered but is not trustworthy right now"
    return 1
  fi
  return 0
}

main() {
  log "dns-aaaa-check start (pihole=$PIHOLE_ADDR canary=$CANARY_HOST)"

  canary_check || exit 1

  if ! hosts=$(list_hosts); then
    log "FAIL: kubectl get ingress failed -- see stderr above"
    exit 1
  fi

  if [ -z "$hosts" ]; then
    # This is not a clean result -- it means the query found nothing, which
    # for a 30-Ingress cluster means the query itself is broken. A check that
    # silently passes because it looked at nothing is the exact failure mode
    # this job exists to prevent, so it must fail, not exit 0.
    log "FAIL: no Ingress hosts found -- the query returned nothing, not a clean cluster"
    exit 1
  fi

  host_count=$(printf '%s\n' "$hosts" | grep -c .)

  leaking_hosts=""
  leak_count=0
  error_hosts=""
  error_count=0
  for host in $hosts; do
    addrs=$(aaaa_for_host "$host")
    rc=$?
    if [ "$rc" -ne 0 ]; then
      # The resolver failed for this one host specifically (mid-run outage,
      # or a single flaky query) even though the canary passed at startup.
      # This must never be folded into "clean" -- an unanswered query and a
      # genuinely absent AAAA record are indistinguishable by output alone,
      # so an unanswered query has to be reported as its own outcome.
      error_count=$((error_count + 1))
      error_hosts="$error_hosts $host"
      log "ERROR: resolver failed for $host via $PIHOLE_ADDR (nslookup rc=$rc) -- not counted as clean"
      continue
    fi
    if [ -n "$addrs" ]; then
      leak_count=$((leak_count + 1))
      leaking_hosts="$leaking_hosts $host"
      log "LEAK: $host resolves AAAA via $PIHOLE_ADDR -> $(printf '%s' "$addrs" | tr '\n' ' ')"
    fi
  done

  if [ "$leak_count" -eq 0 ] && [ "$error_count" -eq 0 ]; then
    log "OK: queried $host_count hosts, none leaking"
    exit 0
  fi

  if [ "$error_count" -gt 0 ]; then
    echo ""
    echo "=== DNS AAAA CHECK INCOMPLETE: resolver failed for $error_count of $host_count hosts ==="
    echo "The hosts below got no answer from Pi-hole at all -- that is NOT the same as a clean"
    echo "'no AAAA record' result. They were skipped, not cleared, and need a re-check:"
    for host in $error_hosts; do
      echo "  $host"
    done
    echo ""
  fi

  if [ "$leak_count" -gt 0 ]; then
    echo ""
    echo "=== DNS AAAA LEAK: $leak_count of $host_count Ingress hosts return AAAA from Pi-hole ($PIHOLE_ADDR) ==="
    echo "Each host below should be A-only on the LAN but resolves an upstream (Cloudflare) IPv6"
    echo "address instead, so an IPv6-capable LAN client hairpins out through the tunnel."
    echo ""
    echo "Remediation -- pihole1 (192.168.100.2) ONLY, nebula-sync replicates to pihole2 hourly:"
    echo "  1. Read the CURRENT array first -- misc.dnsmasq_lines is a REPLACE, not an append:"
    echo "       ssh pihole1 sudo pihole-FTL --config misc.dnsmasq_lines"
    echo "  2. Write it back with every existing entry carried forward, plus one new line per"
    echo "     leaking host below:"
    for host in $leaking_hosts; do
      echo "       local=/$host/"
    done
    echo "     e.g.: sudo pihole-FTL --config misc.dnsmasq_lines '[ ...existing entries..., \"local=/<host>/\" ]'"
    echo "  3. ssh pihole1 sudo pihole reloaddns"
    echo "  4. nebula-sync (hourly cron) replicates pihole1 -> pihole2 -- no action needed there"
    echo ""
  fi

  log "dns-aaaa-check done: $leak_count leaking, $error_count resolver error(s) of $host_count hosts"
  exit 1
}

[ -n "${DNS_AAAA_CHECK_TEST:-}" ] || main "$@"
