#!/usr/bin/env bash
# Put values into the secrets that Terraform creates (infra/main.tf). Terraform manages the
# secrets but never their values, which would otherwise sit in plain text in its state.
# Safe to rerun: a secret that already has a value is left alone.
#
#   scripts/set-secrets.sh
#
# Generates the API keys (one per caller) and asks, with hidden input, for the OpenAI API
# key and the Neon connection string.
set -euo pipefail

project="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$project" ]] || { echo "No project set: gcloud config set project PROJECT_ID" >&2; exit 1; }

has_value() {
  [[ -n "$(gcloud secrets versions list "$1" --project "$project" --filter state:ENABLED \
    --limit 1 --format 'value(name)')" ]]
}
add_value() {  # value on stdin
  gcloud secrets versions add "$1" --project "$project" --data-file - >/dev/null
  echo "$1: value added"
}
ask() {  # hidden input
  local value
  read -rsp "$1: " value
  echo >&2
  printf '%s' "$value"
}

for secret in myapis-api-keys myapis-openai-key myapis-knowledge-db-url; do
  gcloud secrets describe "$secret" --project "$project" >/dev/null 2>&1 || {
    echo "Secret $secret doesn't exist yet: run terraform -chdir=infra apply first" >&2
    exit 1
  }
done

if has_value myapis-api-keys; then
  echo "myapis-api-keys: already has a value"
else
  # One key per caller, so the request log names the caller and each can be revoked alone.
  recorder_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  mcp_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'recorder:%s,mcp:%s' "$recorder_key" "$mcp_key" | add_value myapis-api-keys
  echo "  New API keys, one per caller. Keep them somewhere safe:"
  echo "    recorder: $recorder_key   (goes in myRecorder's config.toml)"
  echo "    mcp:      $mcp_key"
fi

if has_value myapis-openai-key; then
  echo "myapis-openai-key: already has a value"
else
  key="$(ask "OpenAI API key")"
  [[ "$key" == sk-* ]] || { echo "That doesn't look like an OpenAI key (sk-...)" >&2; exit 1; }
  printf '%s' "$key" | add_value myapis-openai-key
fi

if has_value myapis-knowledge-db-url; then
  echo "myapis-knowledge-db-url: already has a value"
else
  url="$(ask "Neon connection string")"
  [[ "$url" == postgres*://* ]] || { echo "Expected postgresql://..." >&2; exit 1; }
  if [[ "$url" == *-pooler.* ]]; then
    # knowledge creates tables and holds connections open, which Neon's pooler doesn't
    # support; the direct host is the same name without "-pooler".
    url="${url/-pooler./.}"
    echo "Using the direct (non-pooled) connection: removed -pooler from the host."
  fi
  printf '%s' "$url" | add_value myapis-knowledge-db-url
fi

echo "API keys can be read again any time with:"
echo "  gcloud secrets versions access latest --secret myapis-api-keys"
