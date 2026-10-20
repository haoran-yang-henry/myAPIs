locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
  # Only the secrets themselves: their values are added with scripts/set-secrets.sh,
  # so they never end up in the Terraform state.
  secrets = ["myapis-api-keys", "myapis-openai-key", "myapis-knowledge-db-url"]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)
  service  = each.value
  # Leave the APIs on if this configuration is ever destroyed.
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "myapis"
  location      = var.region
  format        = "DOCKER"
  description   = "myAPIs service images"

  # Stored images are billed: keep the 3 newest of each service, delete the rest.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      tag_state = "ANY"
    }
  }

  depends_on = [google_project_service.apis]
}

# The services run as this account (see scripts/deploy.sh). It can read the secrets
# and use the transcriber bucket, nothing else.
resource "google_service_account" "runtime" {
  account_id   = "myapis-runtime"
  display_name = "myAPIs services on Cloud Run"
  depends_on   = [google_project_service.apis]
}

resource "google_storage_bucket" "transcriber" {
  name                        = "${var.project_id}-transcriber"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Audio is deleted right after transcription; this sweeps up anything left behind.
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "runtime_objects" {
  bucket = google_storage_bucket.transcriber.name
  role   = "roles/storage.objectUser"
  member = google_service_account.runtime.member
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(local.secrets)
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  for_each  = google_secret_manager_secret.secrets
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.runtime.member
}
