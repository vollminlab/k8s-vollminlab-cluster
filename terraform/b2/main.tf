resource "b2_bucket" "velero" {
  bucket_name = "vollminlab-k8s-backups"
  bucket_type = "allPrivate"

  # The bucket has default SSE-B2 encryption enabled at rest. Declaring it
  # matches live state; omitting the block made every plan try to strip it
  # (algorithm/mode -> null), causing perpetual drift.
  default_server_side_encryption {
    mode      = "SSE-B2"
    algorithm = "AES256"
  }
}

resource "b2_bucket" "volsync" {
  bucket_name = "vollminlab-k8s-volsync"
  bucket_type = "allPrivate"
}

resource "b2_application_key" "volsync" {
  key_name     = "vollminlab-k8s-volsync"
  capabilities = ["listBuckets", "listFiles", "readFiles", "writeFiles", "deleteFiles"]
  bucket_id    = b2_bucket.volsync.id
}

