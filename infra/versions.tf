terraform {
  required_version = ">= 1.11"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
  }

  # State lives in a GCS bucket created once by hand (see AGENTS.md); its name is
  # passed at init: terraform init -backend-config="bucket=PROJECT_ID-tfstate"
  backend "gcs" {
    prefix = "myapis"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
