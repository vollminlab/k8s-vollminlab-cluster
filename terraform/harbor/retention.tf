# Tag retention for the vollminlab project — the only project with no bound.
#
# Measured 2026-08-10 (live artifact bytes, after a manual GC):
#   dockerhub-proxy 2845 MiB   already bounded: Harbor auto-creates a 7-day
#                              n_days_since_last_pull retention on every proxy
#                              cache project (policy id 1 here, runs daily,
#                              succeeding) — that 2.8 GiB is a true LRU working
#                              set, not a leak.
#   vollminlab       587 MiB   unbounded: every release tag ever pushed is kept
#                              forever (longhorn-rebalancing-controller v0.1.0 ->
#                              v0.4.0, shlink-ingress-controller v0.3.1 -> v0.3.6,
#                              vollmint v0.1.0 -> v0.3.2, masters-league,
#                              b2-exporter, ...).
#   library           46 MiB   negligible.
#
# Growth here is a few artifacts per month, so this is a ceiling, not a cleanup:
# the largest repo currently holds 8 artifacts, so this policy deletes nothing
# today. It exists so the project cannot grow without limit — GC only reclaims
# blobs that no artifact references, and nothing ever stops referencing them
# while every historical tag is retained.
#
# The dockerhub-proxy policy is deliberately NOT imported into terraform: it is
# created and owned by Harbor when a proxy cache project is created, it is
# correct as-is, and adopting it would add a conflict risk for no gain.
resource "harbor_retention_policy" "vollminlab" {
  scope    = harbor_project.vollminlab.id
  schedule = "Daily"

  # Keep the 10 most recently pushed artifacts per repository. Anything not
  # matched by a retain rule is deleted, so one rule covering **/** is the whole
  # policy — with 10 kept, current repos (max 8 artifacts) are untouched.
  rule {
    most_recently_pushed = 10
    repo_matching        = "**"
    tag_matching         = "**"
  }
}
