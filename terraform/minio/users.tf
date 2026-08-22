# MinIO service accounts consumed by other systems.
#
# Every user here carries `ignore_changes = [secret]`, and the reason is the same
# in each case: Terraform does not own these passwords. Each secret is generated
# out of band, stored in 1Password, and delivered to the consuming workload by an
# ExternalSecret. If Terraform managed the value it would rewrite the credential on
# any drift and break the consumer mid-flight -- for velero and cnpg that means
# breaking the backup path, silently, until the next restore attempt.
#
# `ignore_changes` is exactly the construct that hides real drift, so it is worth
# being explicit that this is deliberate rather than inherited. The arr quality
# profiles were inverted for months behind an `ignore_changes` nobody had written
# down; this note exists so the same question does not have to be re-derived here.
#
# To rotate one of these: change it in MinIO, update the 1Password item, then
# force-sync the consuming ExternalSecret. Terraform is not involved.

resource "minio_iam_user" "cnpg_svc" {
  name = "cnpg-svc"
  lifecycle { ignore_changes = [secret] }
}

resource "minio_iam_user" "homepage_monitor" {
  name = "homepage-monitor"
  lifecycle { ignore_changes = [secret] }
}

resource "minio_iam_user" "tofu_svc" {
  name = "tofu-svc"
  lifecycle { ignore_changes = [secret] }
}

resource "minio_iam_user" "velero_svc" {
  name = "velero-svc"
  lifecycle { ignore_changes = [secret] }
}

resource "minio_iam_user_policy_attachment" "cnpg_svc" {
  user_name   = minio_iam_user.cnpg_svc.name
  policy_name = minio_iam_policy.cnpg.name
}

resource "minio_iam_user_policy_attachment" "homepage_monitor" {
  user_name   = minio_iam_user.homepage_monitor.name
  policy_name = minio_iam_policy.homepage_monitor.name
}

resource "minio_iam_user_policy_attachment" "tofu_svc" {
  user_name   = minio_iam_user.tofu_svc.name
  policy_name = minio_iam_policy.tofu_state.name
}

resource "minio_iam_user_policy_attachment" "velero_svc" {
  user_name   = minio_iam_user.velero_svc.name
  policy_name = minio_iam_policy.velero.name
}
