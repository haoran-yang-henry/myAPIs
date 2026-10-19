import hashlib
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import Recording, Transcript


class RecordingStore:
    """Recordings on a plain filesystem; in prod the directory is a Cloud Storage mount.

    Layout per recording:
        <root>/<id>/recording.json
        <root>/<id>/parts/00000, 00001, ...
        <root>/<id>/transcript.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, recording_id: str) -> Path:
        return self.root / recording_id

    def part_path(self, recording_id: str, index: int) -> Path:
        return self._dir(recording_id) / "parts" / f"{index:05d}"

    def create(self, recording: Recording) -> None:
        (self._dir(recording.id) / "parts").mkdir(parents=True)
        self._write_json(self._dir(recording.id) / "recording.json", recording.model_dump_json())

    def get(self, recording_id: str) -> Recording | None:
        path = self._dir(recording_id) / "recording.json"
        if not path.exists():
            return None
        return Recording.model_validate_json(path.read_text())

    def save(self, recording: Recording) -> None:
        recording.updated_at = datetime.now(UTC)
        self._write_json(self._dir(recording.id) / "recording.json", recording.model_dump_json())

    def write_part(self, recording_id: str, index: int, data: bytes) -> None:
        self.part_path(recording_id, index).write_bytes(data)

    def missing_parts(self, recording: Recording) -> list[int]:
        """Indexes of parts that are absent or have the wrong size."""
        missing = []
        for index in range(recording.parts_total):
            path = self.part_path(recording.id, index)
            if not path.exists() or path.stat().st_size != recording.expected_part_size(index):
                missing.append(index)
        return missing

    def sha256(self, recording: Recording) -> str:
        digest = hashlib.sha256()
        for index in range(recording.parts_total):
            with self.part_path(recording.id, index).open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    def save_transcript(self, transcript: Transcript) -> None:
        path = self._dir(transcript.recording_id) / "transcript.json"
        self._write_json(path, transcript.model_dump_json())

    def get_transcript(self, recording_id: str) -> Transcript | None:
        path = self._dir(recording_id) / "transcript.json"
        if not path.exists():
            return None
        return Transcript.model_validate_json(path.read_text())

    def delete_audio(self, recording_id: str) -> None:
        shutil.rmtree(self._dir(recording_id) / "parts", ignore_errors=True)

    def delete(self, recording_id: str) -> None:
        shutil.rmtree(self._dir(recording_id), ignore_errors=True)

    @staticmethod
    def _write_json(path: Path, content: str) -> None:
        # Write-then-rename so readers never see a half-written file.
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(content)
        os.replace(tmp, path)
