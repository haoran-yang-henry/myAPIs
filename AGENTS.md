# Working on myAPIs

For whoever maintains this repository, people and coding agents alike: layout, conventions, local development, and deploys. What the project does and how to try it is in [README.md](README.md).

myAPIs is the capability layer of a personal AI toolchain. Each directory under `services/` is an HTTP service with its own image and its own Cloud Run deployment. myRecorder, a Windows tray recorder, feeds it; myMCP, a planned MCP server, will expose these services as tools for AI clients.

## Layout

```
myAPIs/
├── libs/common/            # shared by every service: app factory, API key auth, logging, error shape, base settings
├── services/
│   ├── transcriber/        # a whole recording to text
│   ├── knowledge/          # chunking, embeddings, search
│   └── _template/          # template for new services (copied by scripts/new-service.sh; directories starting with _ are never deployed)
│       ├── src/template_svc/
│       │   ├── main.py     # builds the app
│       │   ├── config.py   # the service's settings
│       │   └── routes.py   # the service's endpoints
│       ├── tests/
│       ├── Dockerfile      # build context is the repository root
│       └── deploy.env      # Cloud Run resources: memory, timeout, concurrency, instance cap, secrets
├── scripts/
│   ├── new-service.sh      # create a service from the template
│   ├── setup-gcp.sh        # one-time GCP setup, safe to rerun
│   └── deploy.sh           # build and deploy one service
├── .github/workflows/      # ci.yml: lint and tests; deploy.yml: deploys only the services that changed
├── docker-compose.yml      # runs the services and Postgres locally, from the images Cloud Run uses
└── pyproject.toml          # uv workspace root; one uv.lock pins every dependency
```

## Conventions every service follows

`myapis_common.create_app` implements these, so a new service gets them without extra code:

| Convention | Details |
|---|---|
| `GET /health` | Public health check. Not `/healthz`, because Cloud Run reserves paths ending in `z`. |
| `/v1/...` | The service's endpoints. Every request needs an `X-API-Key` header. |
| Error shape | Always `{"error": {"code", "message", "request_id"}}`, including 404, 422, and 500. |
| `X-Request-ID` | Reused from the caller when present, generated otherwise, and written to the logs and the response, so one request can be traced across myMCP and the services. |
| Logs | Readable text when `ENV=local`; JSON that Cloud Logging parses everywhere else. |
| Settings | Environment variables only (`.env` locally). When `ENV` isn't `local`, `API_KEYS` must be set or the service refuses to start. |
| `GET /openapi.json` | The API contract; myMCP generates typed clients from it. |

## Local development

```bash
cp .env.example .env
uv sync --all-packages              # installs every service and the dev tools into .venv
uv run pytest                       # every test; the ones that need a database are skipped

# Include the database tests: start the local Postgres first
docker compose up -d postgres
TEST_DATABASE_URL=postgresql://knowledge:knowledge@localhost:5434/knowledge uv run pytest
uv run ruff check . && uv run ruff format .

# Run one service with reload; its API docs are at http://localhost:8000/docs
uv run uvicorn template_svc.main:app --reload

# Or run every service in containers, as Cloud Run does
docker compose up --build
```

## Add a service

```bash
scripts/new-service.sh web-scraper
```

Then:

1. Write the endpoints in `services/web-scraper/src/web_scraper/routes.py` and the settings in `config.py`.
2. Add Python dependencies with `uv add --package web-scraper httpx2`; system packages such as ffmpeg go in its `Dockerfile`.
3. Adjust `deploy.env`: memory, timeout, concurrency, secrets.
4. Add the service to `docker-compose.yml` with a new host port.

## Deploy

### One-time setup

You need a Google Cloud project with billing, a Neon database (its direct connection string, without `-pooler`), and an OpenAI API key.

```bash
gcloud auth login
gcloud config set project PROJECT_ID
gcloud config set run/region europe-west1        # optional; this is the default
scripts/setup-gcp.sh
```

`PROJECT_ID` is your Google Cloud project ID. [setup-gcp.sh](scripts/setup-gcp.sh) is safe to rerun; each run creates only what is missing. It:

- enables the APIs it needs and creates the image repository, which keeps the 3 newest images of each service
- creates the transcriber bucket, which deletes leftover files after 7 days
- creates `myapis-runtime`, the service account the services run as, which can read the three secrets below and use the bucket, and nothing else
- creates three secrets: `myapis-api-keys` (generated, one key per caller, such as `recorder:<key>`), `myapis-openai-key`, and `myapis-knowledge-db-url`; the first run asks for the last two without echoing them
- signs local Docker in to the image repository

The project ID and region live only in gcloud's config, never in the repository.

### Deploy one service by hand

```bash
scripts/deploy.sh transcriber
scripts/deploy.sh knowledge
```

### Deploy from GitHub Actions

On a push to `main`, the tests run, then only the services that changed are deployed; a change to `libs/` or `uv.lock` redeploys all of them. You can also deploy a single service from the **Actions** tab.

To turn this on, run once:

```bash
GITHUB_REPO=haoran-yang-henry/myAPIs scripts/setup-gcp.sh
```

It sets up [Workload Identity Federation](https://github.com/google-github-actions/auth#preferred-direct-workload-identity-federation), so GitHub Actions signs in to Google Cloud without a stored key, and only from this repository. It then writes `GCP_PROJECT`, `GCP_REGION`, `GCP_WIF_PROVIDER`, and `GCP_DEPLOY_SA` to the repository's variables. Without those variables, CI runs the tests and deploys nothing.

### Authentication

The services deploy with `--allow-unauthenticated`: Cloud Run lets every request through, and the app checks `X-API-Key`. Each `API_KEYS` entry is `name:key`. The request log records which caller made each request, and revoking a leaked key means deleting its entry from the secret and redeploying.
