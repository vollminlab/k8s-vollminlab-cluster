#!/bin/sh
# Unit tests for reap.sh. Run: sh reap_test.sh
# Sources reap.sh with REAP_TEST=1 so main() does not run, then stubs kubectl.
# Must pass under dash and busybox ash (the image is alpine/kubectl).
HERE=$(dirname "$0")
REAP_TEST=1 . "$HERE/reap.sh"

fails=0
assert_eq() {
  if [ "$1" = "$2" ]; then echo "ok   - $3"
  else echo "FAIL - $3 (got [$1] want [$2])"; fails=$((fails+1)); fi
}

ATT='pvc-aaa csi-attacher-1,snapshot-controller-dead-1,
pvc-bbb csi-attacher-2,
pvc-ccc snapshot-controller-live-1,'

# --- case 1: no snapshot contents at all -> the snapshot ticket is an orphan,
# the csi-attacher ticket is NOT, and a volume with no snapshot ticket is absent
kubectl() {
  case "$*" in
    *volumesnapshotcontents*) echo "" ;;
    *volumeattachments*jsonpath*) printf '%s\n' "$ATT" ;;
    *) echo "" ;;
  esac
}
orphan_candidates "$WORK".t1
# With no snapshot contents at all, BOTH snapshot tickets are orphans. pvc-bbb
# has only a csi-attacher ticket and must never appear.
assert_eq "$(cat "$WORK".t1)" "pvc-aaa snapshot-controller-dead-1
pvc-ccc snapshot-controller-live-1" "both orphans detected; csi-attacher-only volume ignored"
assert_eq "$(grep -c 'pvc-bbb' "$WORK".t1 || true)" "0" "csi-attacher-only volume never a candidate"

# --- case 2: pvc-ccc HAS a live VolumeSnapshotContent -> never touched
kubectl() {
  case "$*" in
    *volumesnapshotcontents*) echo "pvc-ccc" ;;
    *volumeattachments*jsonpath*) printf '%s\n' "$ATT" ;;
    *) echo "" ;;
  esac
}
orphan_candidates "$WORK".t2
assert_eq "$(grep -c 'pvc-ccc' "$WORK".t2 || true)" "0" "volume with a live VolumeSnapshotContent is skipped"
assert_eq "$(cat "$WORK".t2)" "pvc-aaa snapshot-controller-dead-1" "other orphans still detected"

# --- case 3: every volume has a live content -> nothing is a candidate
kubectl() {
  case "$*" in
    *volumesnapshotcontents*) printf 'pvc-aaa\npvc-ccc\n' ;;
    *volumeattachments*jsonpath*) printf '%s\n' "$ATT" ;;
    *) echo "" ;;
  esac
}
orphan_candidates "$WORK".t3
assert_eq "$(cat "$WORK".t3)" "" "no candidates when all volumes have live snapshots"

# --- case 4: DRY_RUN never patches
patched=no
kubectl() { case "$*" in *patch*) patched=yes ;; esac; echo ""; }
DRY_RUN=true
reap pvc-aaa snapshot-controller-dead-1 >/dev/null 2>&1
assert_eq "$patched" "no" "DRY_RUN does not patch"

cleanup
echo
if [ "$fails" -eq 0 ]; then echo "0 failures"; else echo "$fails failures"; exit 1; fi
