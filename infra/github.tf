# Keyless deploys from GitHub Actions: workflows in var.github_repo, and only there,
# can act as the deployer account. Everything here is skipped while github_repo is empty.

locals {
  github_enabled = var.github_repo != ""
}

resource "google_service_account" "deployer" {
  count        = local.github_enabled ? 1 : 0
  account_id   = "myapis-deployer"
  display_name = "myAPIs deploys from GitHub Actions"
}

resource "google_project_iam_member" "deployer" {
  for_each = local.github_enabled ? toset(["roles/run.admin", "roles/artifactregistry.writer"]) : toset([])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.deployer[0].member
}

# Deploying a service that runs as the runtime account means acting as that account.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  count              = local.github_enabled ? 1 : 0
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer[0].member
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = local.github_enabled ? 1 : 0
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = local.github_enabled ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repo}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_acts_as_deployer" {
  count              = local.github_enabled ? 1 : 0
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repo}"
}
