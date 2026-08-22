#!/bin/sh
# Tests for check.sh. Run: sh check_test.sh
#
# Stubs kubectl and nslookup as real executables on PATH (PATH shims in a temp
# dir), not shell-function overrides -- check.sh calls them directly exactly as
# it will in the CronJob pod, so this exercises the real subprocess/pipe/
# exit-code boundaries (nslookup's "always exits 0" behavior in particular)
# instead of an approximation of them. Mirrors the shape of
# velero/velero-pvb-healer/app/heal_test.sh (stub, assert, no cluster needed).
set -u
HERE=$(dirname "$0")
HERE=$(cd "$HERE" && pwd)

STUBDIR=$(mktemp -d)
trap 'rm -rf "$STUBDIR"' EXIT

# Fake `kubectl get ingress -A -o jsonpath=...`.
# STUB_KUBECTL_FAIL=true simulates an apiserver/RBAC failure (nonzero exit,
# something on stderr, nothing useful on stdout).
# STUB_KUBECTL_HOSTS is printed verbatim as stdout otherwise -- one host per
# line, blank for "found nothing".
cat > "$STUBDIR/kubectl" <<'SCRIPT'
#!/bin/sh
if [ "${STUB_KUBECTL_FAIL:-false}" = "true" ]; then
  echo "stub kubectl: Unauthorized" >&2
  exit 1
fi
if [ -n "${STUB_KUBECTL_HOSTS:-}" ]; then
  printf '%s' "$STUB_KUBECTL_HOSTS"
fi
SCRIPT
chmod +x "$STUBDIR/kubectl"

# Fake `nslookup -type=aaaa <host> <server>`. Emits real nslookup-shaped
# output: two header lines naming the server (which check.sh's tail -n +3
# must skip), then either an AAAA Address: line (leak, if $host is listed in
# STUB_LEAK_HOSTS) or an NXDOMAIN body (clean) -- and always exits 0 either
# way, exactly like real nslookup, which is the whole reason check.sh must
# parse output and can never trust $?.
cat > "$STUBDIR/nslookup" <<'SCRIPT'
#!/bin/sh
host=""
for a in "$@"; do
  case "$a" in
    -*) ;;
    *) [ -n "$host" ] || host="$a" ;;
  esac
done
server="${STUB_PIHOLE_ADDR:-192.168.100.4}"
echo "Server:    $server"
echo "Address:   $server:53"
echo ""
leak=false
for h in ${STUB_LEAK_HOSTS:-}; do
  [ "$h" = "$host" ] && leak=true
done
if [ "$leak" = "true" ]; then
  echo "Name:      $host"
  echo "Address:   2606:4700:3033::abcd"
else
  echo "** server can't find $host: NXDOMAIN"
fi
exit 0
SCRIPT
chmod +x "$STUBDIR/nslookup"

FAILS=0
assert_contains() { # $1=haystack $2=needle $3=msg
  case "$1" in
    *"$2"*) printf 'ok   - %s\n' "$3" ;;
    *) printf 'FAIL - %s (output did not contain [%s])\n--- output ---\n%s\n--------------\n' "$3" "$2" "$1"
       FAILS=$((FAILS + 1)) ;;
  esac
}
assert_rc() { # $1=actual $2=expected $3=msg
  if [ "$1" = "$2" ]; then printf 'ok   - %s\n' "$3"
  else printf 'FAIL - %s (rc got [%s] want [%s])\n' "$3" "$1" "$2"; FAILS=$((FAILS + 1)); fi
}

# Everything the stubs read must be exported so it survives into the child
# `sh check.sh` process and, from there, into the kubectl/nslookup it execs.
export STUB_KUBECTL_FAIL STUB_KUBECTL_HOSTS STUB_LEAK_HOSTS STUB_PIHOLE_ADDR
STUB_KUBECTL_FAIL=false
STUB_KUBECTL_HOSTS=""
STUB_LEAK_HOSTS=""
STUB_PIHOLE_ADDR="192.168.100.4"

chmod +x "$HERE/check.sh"

# check.sh is invoked as an executable (kernel shebang execve), never as
# `sh check.sh`. Under busybox ash, `sh nslookup`-style bare-word lookups
# prefer busybox's own built-in applets (both `sh` and `nslookup` are
# busybox applets -- confirmed via `busybox --list`) over anything found by
# searching $PATH, which would silently defeat the PATH stub above and mask
# it with busybox's real nslookup implementation. Running check.sh as a file
# forces the kernel to exec its #!/bin/sh shebang directly, sidestepping
# that applet-preference entirely, so the stub is honored the same way
# whether check_test.sh itself is run under dash or busybox ash.
run_check() { # sets OUT and RC
  OUT=$(PATH="$STUBDIR:$PATH" "$HERE/check.sh" 2>&1)
  RC=$?
}

# --- 1. clean run: two hosts, neither leaks -> exit 0 ---
STUB_KUBECTL_HOSTS='radarr.vollminlab.com
sonarr.vollminlab.com
'
STUB_LEAK_HOSTS=''
run_check
assert_rc "$RC" "0" "clean run exits 0"
assert_contains "$OUT" "queried 2 hosts, none leaking" "clean run distinguishes 'queried N, none leaking' from 'found nothing'"

# --- 2. one leaking host -> exit non-zero, host + AAAA + remediation named ---
STUB_KUBECTL_HOSTS='radarr.vollminlab.com
authentik.vollminlab.com
'
STUB_LEAK_HOSTS='authentik.vollminlab.com'
run_check
assert_rc "$RC" "1" "a leaking host makes the run fail"
assert_contains "$OUT" "authentik.vollminlab.com" "leaking host is named in the output"
assert_contains "$OUT" "2606:4700:3033::abcd" "leaking host's AAAA address is printed"
assert_contains "$OUT" "local=/authentik.vollminlab.com/" "remediation names the exact local= line to add"
assert_contains "$OUT" "pihole1" "remediation calls out pihole1 specifically"
# The clean host must NOT show up in the leak list.
case "$OUT" in
  *"LEAK: radarr.vollminlab.com"*)
    printf 'FAIL - %s\n' "clean host among two is not misreported as leaking"
    FAILS=$((FAILS + 1)) ;;
  *) printf 'ok   - %s\n' "clean host among two is not misreported as leaking" ;;
esac

# --- 3. zero hosts found: the query itself is broken, not a clean cluster ---
STUB_KUBECTL_HOSTS=''
STUB_LEAK_HOSTS=''
run_check
assert_rc "$RC" "1" "zero Ingress hosts is a failure, not a clean pass"
assert_contains "$OUT" "no Ingress hosts found" "zero-hosts case names itself distinctly from the clean-pass message"

# --- 4. kubectl itself fails ---
STUB_KUBECTL_FAIL=true
STUB_KUBECTL_HOSTS='radarr.vollminlab.com'
run_check
assert_rc "$RC" "1" "a kubectl failure fails the run"
assert_contains "$OUT" "kubectl get ingress failed" "kubectl failure is reported by name, not swallowed"
STUB_KUBECTL_FAIL=false

# --- 5. multiple leaking hosts are all reported, count is correct ---
STUB_KUBECTL_HOSTS='a.vollminlab.com
b.vollminlab.com
c.vollminlab.com
'
STUB_LEAK_HOSTS='a.vollminlab.com c.vollminlab.com'
run_check
assert_rc "$RC" "1" "multiple leaking hosts still fail the run"
assert_contains "$OUT" "2 of 3 Ingress hosts" "leak summary reports the correct leak/total counts"
assert_contains "$OUT" "local=/a.vollminlab.com/" "first leaking host gets its own remediation line"
assert_contains "$OUT" "local=/c.vollminlab.com/" "second leaking host gets its own remediation line"

if [ "$FAILS" -gt 0 ]; then printf '\n%s test(s) failed\n' "$FAILS"; exit 1; fi
printf '\nall tests passed\n'
