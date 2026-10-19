from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

BlockKind = Literal["heading", "paragraph", "list", "table", "code", "utterance"]


class Block(BaseModel):
    """One structural piece of a document (a heading, a paragraph, an utterance...).

    Every source format is turned into blocks first; chunking only ever sees blocks.
    """

    text: str
    kind: BlockKind = "paragraph"
    level: int | None = Field(default=None, ge=1, le=6)  # headings only
    page: int | None = None
    start_s: float | None = None
    end_s: float | None = None
    speaker: str | None = None


class MarkdownContent(BaseModel):
    format: Literal["markdown"]
    text: str


class TranscriptSegment(BaseModel):
    start_s: float
    end_s: float
    text: str
    speaker: str | None = None


class TranscriptContent(BaseModel):
    """A transcript as the transcriber returns it; fields other than segments are ignored."""

    format: Literal["transcript"]
    segments: list[TranscriptSegment]


class BlocksContent(BaseModel):
    """Already-structured content, e.g. from a future PDF extractor."""

    format: Literal["blocks"]
    blocks: list[Block]


Content = Annotated[
    MarkdownContent | TranscriptContent | BlocksContent, Field(discriminator="format")
]


class IngestDocument(BaseModel):
    # Stable id from the source (recording id, file path...). Ingesting it again replaces it.
    external_id: str = Field(min_length=1, max_length=500)
    collection: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=500)
    # When the content happened, e.g. a meeting's start; defaults to the first ingest time.
    occurred_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    content: Content


class DocumentSummary(BaseModel):
    id: UUID
    external_id: str
    collection: str
    title: str
    source_type: str
    occurred_at: datetime
    metadata: dict[str, str]
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class IngestResult(BaseModel):
    status: Literal["created", "updated", "unchanged"]
    document: DocumentSummary


class ChunkOut(BaseModel):
    ord: int
    text: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    start_s: float | None
    end_s: float | None
    speakers: list[str]


class DocumentDetail(DocumentSummary):
    chunks: list[ChunkOut]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collections: list[str] | None = None
    document_ids: list[UUID] | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    # Text a chunk must contain, e.g. a name that semantic search alone might miss.
    must_contain: str | None = Field(default=None, min_length=1, max_length=200)
    top_k: int = Field(default=8, ge=1, le=50)
    # How many adjacent chunks to return on each side of a hit, for context.
    neighbors: int = Field(default=0, ge=0, le=3)


class DocumentRef(BaseModel):
    id: UUID
    external_id: str
    title: str
    collection: str
    occurred_at: datetime


class SearchHit(BaseModel):
    score: float
    document: DocumentRef
    chunk: ChunkOut
    context_before: str = ""
    context_after: str = ""


class SearchResponse(BaseModel):
    hits: list[SearchHit]
