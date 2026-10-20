# transcriber

Turns one whole recording, a meeting from start to stop, into text.

## API

```
POST   /v1/recordings                    create a recording: send its size, get its id and part size
PUT    /v1/recordings/{id}/parts/{n}     upload one part as raw bytes, in any order; uploading a part again replaces it
POST   /v1/recordings/{id}/complete      start transcribing once every part is in; returns 202
GET    /v1/recordings/{id}               poll status and progress
GET    /v1/recordings/{id}/transcript    fetch the transcript
DELETE /v1/recordings/{id}               delete the recording and its transcript
```

Status moves from `uploading` to `queued` to `transcribing`, then to `completed` or `failed`.

Uploads arrive in parts because Cloud Run caps a request at 32 MiB, and an hour of audio often exceeds that. The parts are only how the bytes travel: the service always treats one recording as one file. Each part is 16 MiB (`part_size_bytes`), and the last part holds the rest.

A client must handle these cases (myRecorder does):

- When `complete` returns 409 `parts_missing`, `details.missing_parts` lists the parts that are missing or the wrong size. Upload them, then call `complete` again.
- When the recording was created with a `sha256`, `complete` checks it first. A mismatch returns 422 and leaves the status at `uploading`, so you can upload the parts again.
- `complete` is safe to call again. After a failure, calling it retries with the audio already on the server, so nothing is uploaded twice. If a restart loses a running job, the status stays at `queued` or `transcribing`; once `STALE_JOB_SECONDS` (default 15 minutes) pass without progress, calling `complete` again starts the job over.
- The service deletes the audio as soon as transcription succeeds; the original stays on your own machine.
- Upload a compressed format (m4a, opus, webm, mp3) when you can; any format ffmpeg reads works.

Try it with a recording you already have:

```bash
uv run python services/transcriber/scripts/transcribe_file.py meeting.m4a --language zh-cn
```

The script talks to `TRANSCRIBER_URL` (default `http://localhost:8000`) and sends `MYAPIS_API_KEY` when it's set.

## How it transcribes

1. ffmpeg decodes the whole recording once and finds every pause.
2. The recording is cut into pieces of about 3 minutes, with each cut placed in a pause where possible so no sentence is split, and each piece is re-encoded as 16 kHz mono MP3.
3. Each piece goes to `gpt-transcribe` in its own request (4 at a time by default), and the results join into one transcript in time order. `segments` keeps each piece's start and end time.
4. `speaker` stays empty until speaker diarization is added.

## Settings

| Variable | Default | Notes |
|---|---|---|
| `ASR_PROVIDER` | `openai` | `fake` returns placeholder text without calling any API, for building a client such as the recorder |
| `OPENAI_API_KEY` | | Required in production |
| `OPENAI_BASE_URL` | | Set it to use an OpenAI-compatible provider |
| `ASR_MODEL` | `gpt-transcribe` | |
| `ASR_LANGUAGES`, `ASR_KEYWORDS` | | Default language hints and keywords, comma-separated; each recording can set its own |
| `DATA_DIR` | `.data/transcriber` | Where parts and transcripts live; a mounted Cloud Storage bucket in production |
| `PART_SIZE_BYTES` | 16 MiB | |
| `MAX_RECORDING_BYTES` | 2 GiB | |
| `PIECE_SECONDS`, `PIECE_SEARCH_SECONDS` | 180, 45 | Target piece length; how far before each target cut to look for a pause |
| `TRANSCRIBE_CONCURRENCY` | 4 | |
| `STALE_JOB_SECONDS` | 900 | |

## Deploy

The Terraform configuration in `infra/` creates the bucket, and `scripts/set-secrets.sh` stores the OpenAI key (see the repository's `AGENTS.md`); then deploy with `scripts/deploy.sh transcriber`. The bucket deletes leftover files after 7 days, transcripts included, so fetch each transcript soon after it's done.

The service runs as a single instance. Jobs run inside the process, and the storage mount caches file metadata per instance, so two instances could see a recording in different states. One instance covers personal use.
