from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class CreateRecording(BaseModel):
    """One recording session (start to stop) = one recording, however large."""

    size_bytes: int = Field(gt=0)
    filename: str | None = Field(default=None, max_length=255)
    # Hex SHA-256 of the whole file; checked before transcription when given.
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Language hints such as ["zh-cn", "en"]; defaults to ASR_LANGUAGES.
    languages: list[str] | None = None
    # Names and terms the model should spell correctly; defaults to ASR_KEYWORDS.
    keywords: list[str] | None = None
    # Free-text context about the recording (topic, participants).
    prompt: str | None = Field(default=None, max_length=2000)
    # Caller-defined labels carried through to the transcript, e.g. title or started_at.
    metadata: dict[str, str] = Field(default_factory=dict)


class Progress(BaseModel):
    pieces_done: int = 0
    pieces_total: int = 0


class Recording(BaseModel):
    id: str
    status: Status
    created_at: datetime
    updated_at: datetime
    size_bytes: int
    part_size_bytes: int
    parts_total: int
    filename: str | None = None
    sha256: str | None = None
    languages: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    prompt: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    duration_s: float | None = None
    progress: Progress = Field(default_factory=Progress)
    error: str | None = None

    def expected_part_size(self, index: int) -> int:
        if index < self.parts_total - 1:
            return self.part_size_bytes
        return self.size_bytes - self.part_size_bytes * (self.parts_total - 1)


class Segment(BaseModel):
    start_s: float
    end_s: float
    text: str
    # Stays empty until speaker diarization is added.
    speaker: str | None = None


class Transcript(BaseModel):
    recording_id: str
    model: str
    languages: list[str]
    duration_s: float
    text: str
    segments: list[Segment]
    metadata: dict[str, str] = Field(default_factory=dict)
