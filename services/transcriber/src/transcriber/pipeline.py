import asyncio
import logging
import tempfile
import time
from collections.abc import Coroutine
from pathlib import Path

from . import audio
from .asr import AsrResult, SpeechToText
from .config import Settings
from .models import Progress, Segment, Status, Transcript
from .store import RecordingStore

logger = logging.getLogger(__name__)


class JobRunner:
    """Runs transcription jobs as background tasks of this process, one per recording."""

    def __init__(self) -> None:
        self._running: dict[str, asyncio.Task[None]] = {}

    def is_running(self, recording_id: str) -> bool:
        return recording_id in self._running

    def start(self, recording_id: str, job: Coroutine[None, None, None]) -> None:
        task = asyncio.create_task(job)
        self._running[recording_id] = task
        task.add_done_callback(lambda _: self._running.pop(recording_id, None))


async def transcribe_recording(
    recording_id: str, store: RecordingStore, asr: SpeechToText, settings: Settings
) -> None:
    """Turn an uploaded recording into a transcript.

    On success the audio is deleted (the original stays on the user's machine);
    on failure it is kept so that calling complete again retries without re-uploading.
    """
    recording = store.get(recording_id)
    if recording is None:
        return
    started = time.monotonic()
    try:
        recording.status = Status.TRANSCRIBING
        store.save(recording)

        source = audio.source_of(
            [store.part_path(recording_id, i) for i in range(recording.parts_total)]
        )
        duration, pauses = await audio.analyze(source)
        pieces = audio.plan_pieces(
            duration, pauses, settings.piece_seconds, settings.piece_search_seconds
        )
        recording.duration_s = duration
        recording.progress = Progress(pieces_total=len(pieces))
        store.save(recording)

        limit = asyncio.Semaphore(settings.transcribe_concurrency)

        async def transcribe_piece(path: Path) -> AsrResult:
            async with limit:
                result = await asr.transcribe(
                    path,
                    languages=recording.languages,
                    keywords=recording.keywords,
                    prompt=recording.prompt,
                )
            recording.progress.pieces_done += 1
            store.save(recording)
            return result

        with tempfile.TemporaryDirectory(prefix="transcriber-") as tmp:
            files = await audio.split(source, pieces, Path(tmp))
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(transcribe_piece(path)) for path in files]
        results = [task.result() for task in tasks]

        segments = [
            Segment(start_s=round(piece.start_s, 3), end_s=round(piece.end_s, 3), text=result.text)
            for piece, result in zip(pieces, results, strict=True)
            if result.text
        ]
        store.save_transcript(
            Transcript(
                recording_id=recording_id,
                model=asr.model,
                languages=list(dict.fromkeys(code for r in results for code in r.languages)),
                duration_s=round(duration, 3),
                text="\n".join(segment.text for segment in segments),
                segments=segments,
                metadata=recording.metadata,
            )
        )
        store.delete_audio(recording_id)
        recording.status = Status.COMPLETED
        recording.error = None
        store.save(recording)
        logger.info(
            "Transcribed recording %s: %.0f s of audio in %d pieces, took %.0f s",
            recording_id,
            duration,
            len(pieces),
            time.monotonic() - started,
        )
    except Exception as exc:
        cause = exc.exceptions[0] if isinstance(exc, ExceptionGroup) else exc
        logger.error("Transcription failed for recording %s", recording_id, exc_info=cause)
        if store.get(recording_id) is None:  # deleted meanwhile
            return
        recording.status = Status.FAILED
        recording.error = f"{type(cause).__name__}: {cause}"[:1000]
        store.save(recording)
