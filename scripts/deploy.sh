#!/usr/bin/env bash
# Build one service's image, push it to Artifact Registry and deploy it to Cloud Run.
# Usage: scripts/deploy.sh <service>
# Project and region come from GCP_PROJECT/GCP_REGION, or else from gcloud's config
# (gcloud config set project ... / run/region ...). Run scripts/setup-gcp.sh once first.
set -euo pipefail
cd "$(dirname "$0")/.."

service="${1:?usage: scripts/deploy.sh <service>}"
dir="services/$service"
[[ "$service" != _* ]] || { echo "refusing to deploy $service (templates are not services)" >&2; exit 1; }
[[ -f "$dir/Dockerfile" && -f "$dir/deploy.env" ]] || { echo "no deployable service at $dir" >&2; exit 1; }
GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
GCP_REGION="${GCP_REGION:-$(gcloud config get-value run/region 2>/dev/null)}"
GCP_REGION="${GCP_REGION:-europe-west1}"
: "${GCP_PROJECT:?set GCP_PROJECT or run: gcloud config set project <project-id>}"
repo="${AR_REPO:-myapis}"

# shellcheck source=/dev/null
source "$dir/deploy.env"

tag="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
image="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$repo/$service:$tag"

docker build --platform linux/amd64 -f "$dir/Dockerfile" -t "$image" .
docker push "$image"

# Public at the Cloud Run level; the app itself enforces X-API-Key on /v1.
gcloud run deploy "$service" \
  --project "$GCP_PROJECT" \
  --region "$GCP_REGION" \
  --image "$image" \
  --service-account "myapis-runtime@$GCP_PROJECT.iam.gserviceaccount.com" \
  --memory "$MEMORY" \
  --cpu "$CPU" \
  --timeout "$TIMEOUT" \
  --concurrency "$CONCURRENCY" \
  --min-instances "$MIN_INSTANCES" \
  --max-instances "$MAX_INSTANCES" \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "$SECRETS" \
  --allow-unauthenticated \
  ${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"} # optional bash array in deploy.env, e.g. volumes
