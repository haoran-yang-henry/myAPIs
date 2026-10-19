import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from myapis_common import ApiError

from .chunking import Chunk, chunk_blocks
from .config import Settings, SettingsDep
from .embeddings import Embedder
from .formats import to_blocks
from .models import (
    Block,
    ChunkOut,
    DocumentDetail,
    DocumentRef,
    DocumentSummary,
    IngestDocument,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from .store import KnowledgeStore

router = APIRouter(tags=["knowledge"])


def get_store(request: Request) -> KnowledgeStore:
    return request.app.state.store


def get_embedder(request: Request) -> Embedder:
    embedder = request.app.state.embedder
    if embedder is None:
        raise ApiError(
            503, "embeddings_not_configured", "Embeddings have no credentials configured"
        )
    return embedder


StoreDep = Annotated[KnowledgeStore, Depends(get_store)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


@router.post("/documents", status_code=201)
def ingest_document(
    body: IngestDocument,
    response: Response,
    store: StoreDep,
    embedder: EmbedderDep,
    settings: SettingsDep,
) -> IngestResult:
    """Add a document, or replace the one with the same external_id.

    Re-sending identical content is cheap: nothing is re-embedded.
    """
    blocks = to_blocks(body.content)
    if sum(len(block.text) for block in blocks) > settings.max_document_chars:
        raise ApiError(
            413, "document_too_large", f"Limit is {settings.max_document_chars} characters"
        )
    chunks = chunk_blocks(
        blocks,
        target=settings.chunk_target_tokens,
        max_tokens=settings.chunk_max_tokens,
        overlap=settings.chunk_overlap_tokens,
    )
    if not chunks:
        raise ApiError(422, "empty_document", "The document has no text")

    content_hash = _content_hash(body.title, blocks, settings, embedder.model)
    existing = store.find(body.external_id)
    occurred_at = body.occurred_at or (existing["occurred_at"] if existing else datetime.now(UTC))
    if existing and existing["content_hash"] == content_hash:
        row = store.update_details(existing["id"], body.collection, occurred_at, body.metadata)
        response.status_code = 200
        return IngestResult(status="unchanged", document=DocumentSummary(**row))

    vectors = embedder.embed([_embedding_text(body.title, chunk) for chunk in chunks])
    row, inserted = store.upsert(
        external_id=body.external_id,
        collection=body.collection,
        title=body.title,
        source_type=body.content.format,
        occurred_at=occurred_at,
        metadata=body.metadata,
        content_hash=content_hash,
        chunks=chunks,
        vectors=vectors,
        embedding_model=embedder.model,
    )
    response.status_code = 201 if inserted else 200
    return IngestResult(
        status="created" if inserted else "updated", document=DocumentSummary(**row)
    )


@router.get("/documents")
def list_documents(
    store: StoreDep,
    collection: str | None = None,
    external_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentSummary]:
    """Documents, most recent first."""
    rows = store.list_documents(
        collection=collection, external_id=external_id, limit=limit, offset=offset
    )
    return [DocumentSummary(**row) for row in rows]


@router.get("/documents/{document_id}")
def get_document(document_id: UUID, store: StoreDep) -> DocumentDetail:
    found = store.get(document_id)
    if found is None:
        raise ApiError(404, "document_not_found", f"No document {document_id}")
    document, chunks = found
    return DocumentDetail(**document, chunks=[ChunkOut(**chunk) for chunk in chunks])


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: UUID, store: StoreDep) -> None:
    if not store.delete(document_id):
        raise ApiError(404, "document_not_found", f"No document {document_id}")


@router.post("/search")
def search(body: SearchRequest, store: StoreDep, embedder: EmbedderDep) -> SearchResponse:
    """The chunks closest in meaning to the query, best first.

    Returns passages, not answers: the caller (e.g. an LLM via myMCP) writes the answer.
    """
    (vector,) = embedder.embed([body.query])
    hits = []
    for row in store.search(vector, body, embedder.model):
        before, after = (
            store.neighbors(row["document_id"], row["ord"], body.neighbors)
            if body.neighbors
            else ([], [])
        )
        hits.append(
            SearchHit(
                score=round(row["score"], 4),
                document=DocumentRef(id=row["document_id"], **_pick(row, DocumentRef, skip={"id"})),
                chunk=ChunkOut(**_pick(row, ChunkOut)),
                context_before="\n\n".join(before),
                context_after="\n\n".join(after),
            )
        )
    return SearchResponse(hits=hits)


def _embedding_text(title: str, chunk: Chunk) -> str:
    # The title and heading path tell the embedding what the passage is about.
    header = "\n".join(part for part in (title, " > ".join(chunk.heading_path)) if part)
    return f"{header}\n\n{chunk.text}"


def _content_hash(title: str, blocks: list[Block], settings: Settings, model: str) -> str:
    """Changes whenever the stored chunks or their vectors would change."""
    payload = {
        "title": title,
        "blocks": [block.model_dump() for block in blocks],
        "chunking": [
            settings.chunk_target_tokens,
            settings.chunk_max_tokens,
            settings.chunk_overlap_tokens,
        ],
        "model": model,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _pick(row: dict[str, Any], model: type, skip: set[str] = frozenset()) -> dict[str, Any]:
    return {name: row[name] for name in model.model_fields if name not in skip}
