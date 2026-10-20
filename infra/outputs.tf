output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "transcriber_bucket" {
  value = google_storage_bucket.transcriber.name
}

output "github_actions_variables" {
  description = "Repository variables that .github/workflows/deploy.yml reads; empty while github_repo is unset."
  value = local.github_enabled ? {
    GCP_PROJECT      = var.project_id
    GCP_REGION       = var.region
    GCP_WIF_PROVIDER = google_iam_workload_identity_pool_provider.github[0].name
    GCP_DEPLOY_SA    = google_service_account.deployer[0].email
  } : {}
}
