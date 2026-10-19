#!/usr/bin/env bash
# Set up Google Cloud for myAPIs. Run it once; running it again is safe and only creates
# what is missing.
#
#   gcloud auth login
#   gcloud config set project <project-id>
#   gcloud config set run/region europe-west1      # optional; this is the default
#   scripts/setup-gcp.sh
#
# For deploys from GitHub Actions, also pass the repository:
#   GITHUB_REPO=<owner>/myAPIs scripts/setup-gcp.sh
#
# The first run asks (hidden input) for the OpenAI API key and the Neon connection string.
set -euo pipefail
cd "$(dirname "$0")/.."

project="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
region="${GCP_REGION:-$(gcloud config get-value run/region 2>/dev/null)}"
region="${region:-europe-west1}"
repo="${AR_REPO:-myapis}"
[[ -n "$project" ]] || { echo "No project set: gcloud config set project <project-id>" >&2; exit 1; }

number="$(gcloud projects describe "$project" --format='value(projectNumber)')"
# The services run as this account (see deploy.sh); it can read its secrets and use the bucket.
runtime_sa="myapis-runtime@$project.iam.gserviceaccount.com"
bucket="gs://$project-transcriber"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

step() { printf '\n== %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

step "Project $project ($number), region $region"

step "Enabling APIs (the first time takes a minute)"
gcloud services enable --project "$project" \
  run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

step "Service account the services run as: $runtime_sa"
exists gcloud iam service-accounts describe "$runtime_sa" --project "$project" ||
  gcloud iam service-accounts create myapis-runtime --project "$project" \
    --display-name "myAPIs services on Cloud Run"

step "Image repository: $region-docker.pkg.dev/$project/$repo"
exists gcloud artifacts repositories describe "$repo" --project "$project" --location "$region" ||
  gcloud artifacts repositories create "$repo" --project "$project" --location "$region" \
    --repository-format docker --description "myAPIs service images"
# Stored images are billed: keep the 3 newest of each service, delete the rest.
cat > "$tmp/cleanup.json" <<'EOF'
[
  {"name": "keep-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies "$repo" --project "$project" \
  --location "$region" --policy "$tmp/cleanup.json" --quiet >/dev/null

step "Bucket for transcriber uploads: $bucket"
exists gcloud storage buckets describe "$bucket" ||
  gcloud storage buckets create "$bucket" --project "$project" --location "$region" \
    --uniform-bucket-level-access --public-access-prevention
# Audio is deleted right after transcription; this sweeps up anything left behind.
echo '{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 7}}]}' > "$tmp/lifecycle.json"
gcloud storage buckets update "$bucket" --lifecycle-file "$tmp/lifecycle.json" >/dev/null
gcloud storage buckets add-iam-policy-binding "$bucket" \
  --member "serviceAccount:$runtime_sa" --role roles/storage.objectUser >/dev/null

step "Secrets"
secret_exists() { exists gcloud secrets describe "$1" --project "$project"; }
create_secret() {  # value on stdin
  gcloud secrets create "$1" --project "$project" --replication-policy automatic \
    --data-file - >/dev/null
  echo "$1: created"
}
ask() {  # hidden input
  local value
  read -rsp "$1: " value
  echo >&2
  printf '%s' "$value"
}
if secret_exists myapis-api-keys; then
  echo "myapis-api-keys: exists"
else
  # One key per caller, so the request log names the caller and each can be revoked alone.
  recorder_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  mcp_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'recorder:%s,mcp:%s' "$recorder_key" "$mcp_key" | create_secret myapis-api-keys
  echo "  New API keys, one per caller. Keep them somewhere safe:"
  echo "    recorder: $recorder_key   (goes in myRecorder's config.toml)"
  echo "    mcp:      $mcp_key"
fi
if secret_exists myapis-openai-key; then
  echo "myapis-openai-key: exists"
else
  key="$(ask "OpenAI API key")"
  [[ "$key" == sk-* ]] || { echo "That doesn't look like an OpenAI key (sk-...)" >&2; exit 1; }
  printf '%s' "$key" | create_secret myapis-openai-key
fi
if secret_exists myapis-knowledge-db-url; then
  echo "myapis-knowledge-db-url: exists"
else
  url="$(ask "Neon connection string")"
  [[ "$url" == postgres*://* ]] || { echo "Expected postgresql://..." >&2; exit 1; }
  if [[ "$url" == *-pooler.* ]]; then
    # knowledge creates tables and holds connections open, which Neon's pooler doesn't
    # support; the direct host is the same name without "-pooler".
    url="${url/-pooler./.}"
    echo "Using the direct (non-pooled) connection: removed -pooler from the host."
  fi
  printf '%s' "$url" | create_secret myapis-knowledge-db-url
fi
for secret in myapis-api-keys myapis-openai-key myapis-knowledge-db-url; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$project" \
    --member "serviceAccount:$runtime_sa" --role roles/secretmanager.secretAccessor >/dev/null
done

if [[ -n "${GITHUB_REPO:-}" ]]; then
  step "Keyless deploys from GitHub Actions ($GITHUB_REPO)"
  deployer="myapis-deployer@$project.iam.gserviceaccount.com"
  exists gcloud iam service-accounts describe "$deployer" --project "$project" ||
    gcloud iam service-accounts create myapis-deployer --project "$project" \
      --display-name "myAPIs deploys from GitHub Actions"
  for role in roles/run.admin roles/artifactregistry.writer; do
    gcloud projects add-iam-policy-binding "$project" --member "serviceAccount:$deployer" \
      --role "$role" --condition None >/dev/null
  done
  # Deploying a service that runs as the runtime account means acting as that account.
  gcloud iam service-accounts add-iam-policy-binding "$runtime_sa" --project "$project" \
    --member "serviceAccount:$deployer" --role roles/iam.serviceAccountUser >/dev/null

  pool="projects/$number/locations/global/workloadIdentityPools/github"
  exists gcloud iam workload-identity-pools describe github --project "$project" --location global ||
    gcloud iam workload-identity-pools create github --project "$project" --location global \
      --display-name "GitHub Actions"
  exists gcloud iam workload-identity-pools providers describe github --project "$project" \
    --location global --workload-identity-pool github ||
    gcloud iam workload-identity-pools providers create-oidc github --project "$project" \
      --location global --workload-identity-pool github --display-name "GitHub" \
      --issuer-uri https://token.actions.githubusercontent.com \
      --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition "assertion.repository == '$GITHUB_REPO'"
  # Only workflows in this repository may act as the deployer.
  gcloud iam service-accounts add-iam-policy-binding "$deployer" --project "$project" \
    --role roles/iam.workloadIdentityUser \
    --member "principalSet://iam.googleapis.com/$pool/attribute.repository/$GITHUB_REPO" >/dev/null

  variables=(
    "GCP_PROJECT=$project"
    "GCP_REGION=$region"
    "GCP_WIF_PROVIDER=$pool/providers/github"
    "GCP_DEPLOY_SA=$deployer"
  )
  if gh repo view "$GITHUB_REPO" >/dev/null 2>&1; then
    for variable in "${variables[@]}"; do
      gh variable set "${variable%%=*}" --repo "$GITHUB_REPO" --body "${variable#*=}"
    done
    echo "Set the deploy variables on $GITHUB_REPO."
  else
    echo "Add these under the repository's Settings > Secrets and variables > Actions > Variables:"
    printf '  %s\n' "${variables[@]}"
  fi
fi

step "Docker login for pushing images from this machine"
gcloud auth configure-docker "$region-docker.pkg.dev" --quiet >/dev/null 2>&1
echo "ok"

step "Done. Deploy with: scripts/deploy.sh transcriber && scripts/deploy.sh knowledge"
echo "API keys can be read again any time with:"
echo "  gcloud secrets versions access latest --secret myapis-api-keys"
echo
echo "Also, if not done yet: a budget alert (Billing > Budgets & alerts) and a monthly"
echo "spending limit on the OpenAI project."
