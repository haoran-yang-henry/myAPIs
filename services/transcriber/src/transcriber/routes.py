import asyncio
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from myapis_common import ApiError

from .asr import SpeechToText
from .config import Settings, SettingsDep
from .models import CreateRecording, Recording, Status, Transcript
from .pipeline import JobRunner, transcribe_recording
from .store import RecordingStore

router = APIRouter(tags=["transcriber"])

RecordingId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def get_store(request: Request) -> RecordingStore:
    return request.app.state.store


StoreDep = Annotated[RecordingStore, Depends(get_store)]


def _get_or_404(store: RecordingStore, recording_id: str) -> Recording:
    recording = store.get(recording_id)
    if recording is None:
        raise ApiError(404, "recording_not_found", f"No recording {recording_id}")
    return recording


@router.post("/recordings", status_code=201)
def create_recording(body: CreateRecording, store: StoreDep, settings: SettingsDep) -> Recording:
    """Start uploading one recording.

    Send the file as `parts_total` parts of `part_size_bytes` (the last one holds the rest),
    then call complete.
    """
    if body.size_bytes > settings.max_recording_bytes:
        raise ApiError(
            413,
            "recording_too_large",
            f"Recordings are limited to {settings.max_recording_bytes} bytes",
        )
    now = datetime.now(UTC)
    recording = Recording(
        id=uuid.uuid4().hex,
        status=Status.UPLOADING,
        created_at=now,
        updated_at=now,
        size_bytes=body.size_bytes,
        part_size_bytes=settings.part_size_bytes,
        parts_total=math.ceil(body.size_bytes / settings.part_size_bytes),
        filename=body.filename,
        sha256=body.sha256,
        languages=settings.default_languages if body.languages is None else body.languages,
        keywords=settings.default_keywords if body.keywords is None else body.keywords,
        prompt=body.prompt,
        metadata=body.metadata,
    )
    store.create(recording)
    return recording


@router.put("/recordings/{recording_id}/parts/{index}", status_code=204)
async def upload_part(
    recording_id: RecordingId,
    index: Annotated[int, Path(ge=0)],
    request: Request,
    store: StoreDep,
) -> None:
    """Upload one part as the raw request body. Uploading the same part again replaces it."""
    recording = await asyncio.to_thread(_get_or_404, store, recording_id)
    if recording.status is not Status.UPLOADING:
        raise ApiError(409, "not_uploading", f"Recording is {recording.status}, not uploading")
    if index >= recording.parts_total:
        raise ApiError(
            400, "part_out_of_range", f"Part index must be below {recording.parts_total}"
        )
    expected = recording.expected_part_size(index)
    data = await _read_body(request, limit=expected)
    if len(data) != expected:
        raise ApiError(
            400, "part_size_mismatch", f"Part {index} must be {expected} bytes, got {len(data)}"
        )
    await asyncio.to_thread(store.write_part, recording_id, index, data)


@router.post("/recordings/{recording_id}/complete", status_code=202)
async def complete_recording(
    recording_id: RecordingId, request: Request, store: StoreDep, settings: SettingsDep
) -> Recording:
    """Start transcribing once every part is uploaded, then poll the recording for progress.

    Safe to call again: it retries a failed or lost job, and otherwise returns the current state.
    """
    asr: SpeechToText | None = request.app.state.asr
    jobs: JobRunner = request.app.state.jobs
    if asr is None:
        raise ApiError(503, "asr_not_configured", "Speech-to-text has no credentials configured")
    async with request.app.state.complete_lock:
        recording = await asyncio.to_thread(_get_or_404, store, recording_id)
        if not _can_start(recording, jobs, settings):
            return recording
        missing = await asyncio.to_thread(store.missing_parts, recording)
        if missing:
            raise ApiError(
                409,
                "parts_missing",
                f"{len(missing)} part(s) missing or the wrong size",
                details={"missing_parts": missing},
            )
        if recording.sha256 and recording.status is Status.UPLOADING:
            if await asyncio.to_thread(store.sha256, recording) != recording.sha256:
                raise ApiError(
                    422,
                    "checksum_mismatch",
                    "Uploaded bytes don't match sha256; re-upload the parts",
                )
        recording.status = Status.QUEUED
        recording.error = None
        await asyncio.to_thread(store.save, recording)
        jobs.start(recording_id, transcribe_recording(recording_id, store, asr, settings))
    return recording


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: RecordingId, store: StoreDep) -> Recording:
    """Status and progress of a recording."""
    return _get_or_404(store, recording_id)


@router.get("/recordings/{recording_id}/transcript")
def get_transcript(recording_id: RecordingId, store: StoreDep) -> Transcript:
    recording = _get_or_404(store, recording_id)
    transcript = store.get_transcript(recording_id)
    if transcript is None:
        raise ApiError(409, "transcript_not_ready", f"Recording is {recording.status}")
    return transcript


@router.delete("/recordings/{recording_id}", status_code=204)
def delete_recording(recording_id: RecordingId, request: Request, store: StoreDep) -> None:
    """Delete the recording, any uploaded audio, and its transcript."""
    _get_or_404(store, recording_id)
    if request.app.state.jobs.is_running(recording_id):
        raise ApiError(409, "transcription_running", "Wait for transcription to finish first")
    store.delete(recording_id)


def _can_start(recording: Recording, jobs: JobRunner, settings: Settings) -> bool:
    if recording.status in (Status.UPLOADING, Status.FAILED):
        return True
    if recording.status in (Status.QUEUED, Status.TRANSCRIBING) and not jobs.is_running(
        recording.id
    ):
        # Not running here, so the process that ran it is gone (restart, redeploy).
        # Wait out the stale window in case another instance is still working on it.
        idle = datetime.now(UTC) - recording.updated_at
        return idle > timedelta(seconds=settings.stale_job_seconds)
    return False


async def _read_body(request: Request, *, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(400, "part_size_mismatch", f"Part is larger than {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
