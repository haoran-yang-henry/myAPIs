# myAPIs

Personal HTTP services that turn meeting recordings into searchable text.

Two FastAPI services, each in its own container on Cloud Run: `transcriber` turns a whole recording into a transcript with OpenAI's `gpt-transcribe`, and `knowledge` stores transcripts and Markdown in Postgres with pgvector, then returns the passages that match a question.

> [!NOTE]
> Both services call OpenAI. Set `OPENAI_API_KEY` in `.env` before starting them; without it they still start, but answer `503` to anything that needs OpenAI.

## Install

With Docker, from a checkout:

```bash
cp .env.example .env
docker compose up -d --build transcriber knowledge
```

`transcriber` listens on `localhost:8002` and `knowledge` on `localhost:8003`; each serves its API docs at `/docs`. Compose also starts the Postgres that `knowledge` needs.

## Quickstart

Transcribe a recording, store it, and search it. The scripts run with [uv](https://docs.astral.sh/uv/):

```bash
TRANSCRIBER_URL=http://localhost:8002 uv run python services/transcriber/scripts/transcribe_file.py meeting.m4a --json > transcript.json
uv run python services/knowledge/scripts/kb.py add transcript.json --title "Weekly meeting"
uv run python services/knowledge/scripts/kb.py search "what did we decide"
```

`search` prints the closest passages, each with its score, title, and position in the recording.

## What you can do

- **Transcribe whole meetings:** one recording of up to 2 GiB is uploaded in 16 MiB parts, cut at pauses into pieces of about 3 minutes, and returned as one transcript with each piece's start and end time. The audio is deleted once it is transcribed.
- **Search what was said:** `knowledge` takes transcripts, Markdown, or pre-structured blocks, splits them along headings and sentences, and embeds them with `text-embedding-3-large`. A search can filter by collection, date, or a word the passage must contain, and return neighboring passages for context.
- **Send things again safely:** a document sent again with the same `external_id` replaces the old one, and unchanged content is not embedded twice. A failed transcription restarts without uploading the audio again.

Each service's README documents its API: [transcriber](services/transcriber/README.md), [knowledge](services/knowledge/README.md).

## Deploy to Cloud Run

Needs a Google Cloud project with billing, a Neon database, and `gcloud` signed in with the project selected:

```bash
scripts/setup-gcp.sh
scripts/deploy.sh transcriber
scripts/deploy.sh knowledge
```

`setup-gcp.sh` creates the image repository, the upload bucket, a runtime account limited to those resources, and the secrets, asking for the OpenAI key and the Neon connection string. Each service gets an HTTPS URL, and every request to `/v1` needs an `X-API-Key`.

## Notes

- Clients live in their own repositories: a Windows tray recorder that uploads meetings, and a planned MCP server.
- `transcriber` runs as a single instance, because jobs run inside the process and its storage mount caches file metadata per instance.
- To work on the code, add a service, or change how it deploys, read [AGENTS.md](AGENTS.md).
