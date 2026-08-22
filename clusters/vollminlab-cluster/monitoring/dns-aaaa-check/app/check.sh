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
# one per line. Empty output means no leak.
#
# nslookup exits 0 whether it finds an AAAA record, finds nothing, or hits
# NXDOMAIN -- the exit code can NEVER be used to detect a leak, only the
# parsed output can. `tail -n +3` skips the two header lines that name the
# server itself (whose own "Address:" line is "<server>:53" and would
# otherwise be misread as a leak).
aaaa_for_host() {
  host="$1"
  nslookup -type=aaaa "$host" "$PIHOLE_ADDR" 2>/dev/null \
    | tail -n +3 \
    | awk '/^Address:/{print $2}' \
    | grep -E '^[0-9a-fA-F:]+$'
}

main() {
  log "dns-aaaa-check start (pihole=$PIHOLE_ADDR)"

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
  for host in $hosts; do
    addrs=$(aaaa_for_host "$host")
    if [ -n "$addrs" ]; then
      leak_count=$((leak_count + 1))
      leaking_hosts="$leaking_hosts $host"
      log "LEAK: $host resolves AAAA via $PIHOLE_ADDR -> $(printf '%s' "$addrs" | tr '\n' ' ')"
    fi
  done

  if [ "$leak_count" -eq 0 ]; then
    log "OK: queried $host_count hosts, none leaking"
    exit 0
  fi

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

  log "dns-aaaa-check done: $leak_count of $host_count hosts leaking"
  exit 1
}

[ -n "${DNS_AAAA_CHECK_TEST:-}" ] || main "$@"
