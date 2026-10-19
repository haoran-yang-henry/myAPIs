# knowledge

A personal knowledge base: it stores meeting transcripts and documents, and returns the passages whose meaning matches a question.

It only retrieves. The caller writes the answer, for example Claude behind myMCP; retrieval-augmented generation (RAG) is knowledge and that caller working together.

## API

```
POST   /v1/documents          add a document or replace one
GET    /v1/documents          list documents, newest first; filter by collection or external_id
GET    /v1/documents/{id}     one document with all its chunks
DELETE /v1/documents/{id}
POST   /v1/search             search
```

**Add a document.** `external_id` is a stable id from the source, such as a recording id or a file path. Sending the same id again replaces the document; when the content hasn't changed, nothing is embedded again.

```json
{
  "external_id": "transcriber:4019ffd03f2849c0873ccebb37a5c4cf",
  "collection": "meetings",
  "title": "Weekly meeting",
  "occurred_at": "2026-10-19T15:40:30+02:00",
  "content": {
    "format": "transcript",
    "segments": [{"start_s": 0, "end_s": 27.5, "text": "We ship the transcriber to Cloud Run next week."}]
  }
}
```

`content` takes one of three formats:

- `transcript`: the transcriber's response, passed through as it is.
- `markdown`: Markdown or plain text.
- `blocks`: content that is already structured, kept for a future PDF and Word extractor.

**Search.**

```json
{
  "query": "who owns the budget",
  "collections": ["meetings"],
  "occurred_after": "2026-10-01T00:00:00Z",
  "must_contain": "Wang",
  "top_k": 8,
  "neighbors": 1
}
```

- `must_contain` requires a word in the passage, for names that semantic search alone can miss.
- `neighbors` also returns that many adjacent chunks on each side of each hit, for context.
- Each hit has a similarity score, its document, the original text, its heading path, and either the time range in the meeting or the page range in the document.

## Chunking

Every source format follows the same rules:

1. The content becomes blocks that keep its structure. Markdown becomes headings, paragraphs, lists, tables, and code blocks; a transcript becomes utterances with start and end times.
2. Adjacent blocks are packed into chunks of about 500 tokens, 800 at most. Every heading starts a new chunk, so no chunk crosses into another section.
3. A block that's too long splits at sentence ends (code and tables at line ends), and its time range is shared across the pieces in proportion to their length. Adjacent chunks in a section overlap by about 60 tokens of whole sentences.
4. Each chunk is embedded with the document title and heading path in front of it; searches return the original text.

## Command line

```bash
uv run python services/knowledge/scripts/kb.py add notes.md --collection docs
uv run python services/knowledge/scripts/kb.py add transcript.json   # a myRecorder result
uv run python services/knowledge/scripts/kb.py search "who owns the budget" --collection meetings
```

The script talks to `KNOWLEDGE_URL` (default `http://localhost:8003`) and sends `MYAPIS_API_KEY` when it's set.

## Settings

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | the Postgres in docker-compose | Must be Postgres with pgvector |
| `EMBEDDING_PROVIDER` | `openai` | `fake` computes stand-in vectors locally without calling an API; for tests only |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | |
| `EMBEDDING_DIMENSIONS` | 1536 | Fixed when the table is created; if you change it later, the service refuses to start |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | | Shared with transcriber |
| `CHUNK_TARGET_TOKENS`, `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS` | 500, 800, 60 | |

**Changing the embedding model:** searches compare only vectors made by the same model, so old and new vectors never mix. Add your documents again: the model name is part of each document's content hash, so a new model re-embeds them.

## Local development and tests

```bash
docker compose up -d postgres
uv run uvicorn knowledge.main:app --reload --port 8003
TEST_DATABASE_URL=postgresql://knowledge:knowledge@localhost:5434/knowledge uv run pytest services/knowledge
```

Without `TEST_DATABASE_URL`, the tests that need a database are skipped; CI runs them against a pgvector container. Each test uses a temporary schema that's dropped afterwards.

## Deploy

1. Create a project on [Neon](https://neon.tech) and copy its **direct** connection string: in the **Connect** dialog, turn off **Connection pooling**, so the host doesn't contain `-pooler`. The service creates its tables at startup and holds connections open, and Neon requires a direct connection for both. The service creates the pgvector extension on its first start.
2. Run `scripts/setup-gcp.sh` from the repository root. It asks for the connection string, removes `-pooler` from it if you paste the pooled one, and stores it in Secret Manager.
3. Deploy with `scripts/deploy.sh knowledge`.
