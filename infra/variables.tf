variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, the image repository, and the transcriber bucket."
  type        = string
  default     = "europe-west1"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to deploy through Workload Identity Federation. Empty turns off deploys from GitHub Actions."
  type        = string
  default     = ""
}
