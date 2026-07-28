# Weekly registry garbage collection. GC has to be scheduled explicitly —
# without it every overwritten proxy-cache tag and re-pushed CI tag leaks
# blobs forever (2026-07-22: the 5Gi registry PVC hit 91% with ~2.8Gi of
# orphans; a manual GC with these same parameters freed 3.5GB).
# Harbor v2.1+ GC is online/non-blocking, so running during active work is
# safe. Slot: Saturday 05:00 UTC — clear of VolSync (00-02), Velero (02-04)
# and the Longhorn rebalancer window (13-19). Harbor cron is 6-field
# (seconds first).
resource "harbor_garbage_collection" "weekly" {
  schedule        = "0 0 5 * * 6"
  delete_untagged = true
  workers         = 1
}
