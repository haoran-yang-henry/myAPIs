#!/usr/bin/env bash
# Create a new service from services/_template.
# Usage: scripts/new-service.sh <name>    e.g. scripts/new-service.sh web-scraper
set -euo pipefail
cd "$(dirname "$0")/.."

name="${1:?usage: scripts/new-service.sh <name>}"
[[ "$name" =~ ^[a-z][a-z0-9-]*$ ]] || {
  echo "name must be lowercase letters, digits and hyphens, starting with a letter" >&2
  exit 1
}
pkg="${name//-/_}"
dest="services/$name"
[[ ! -e "$dest" ]] || { echo "$dest already exists" >&2; exit 1; }

cp -r services/_template "$dest"
find "$dest" -name __pycache__ -prune -exec rm -rf {} +
mv "$dest/src/template_svc" "$dest/src/$pkg"
grep -rlE 'template_svc|template-svc|services/_template' "$dest" | xargs sed -i \
  -e "s/template_svc/$pkg/g" \
  -e "s/template-svc/$name/g" \
  -e "s#services/_template#services/$name#g"

uv lock
echo "Created $dest"
echo "Next: write routes in $dest/src/$pkg/routes.py, tune $dest/deploy.env,"
echo "      and add the service to docker-compose.yml."
