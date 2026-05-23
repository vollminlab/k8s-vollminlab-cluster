resource "b2_bucket" "velero" {
  bucket_name = "vollminlab-k8s-backups"
  bucket_type = "allPrivate"
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

