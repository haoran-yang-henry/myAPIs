from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .chunking import Chunk
from .models import SearchRequest

# Applied at startup; IF NOT EXISTS keeps it idempotent. The vector size is fixed
# when the table is created and checked against the settings on every start.
_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text NOT NULL UNIQUE,
    collection text NOT NULL,
    title text NOT NULL,
    source_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{{}}',
    content_hash text NOT NULL,
    chunk_count integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_collection_idx ON documents (collection, occurred_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
    id bigserial PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ord integer NOT NULL,
    text text NOT NULL,
    heading_path text[] NOT NULL DEFAULT '{{}}',
    page_start integer,
    page_end integer,
    start_s real,
    end_s real,
    speakers text[] NOT NULL DEFAULT '{{}}',
    tokens integer NOT NULL,
    embedding vector({dimensions}) NOT NULL,
    embedding_model text NOT NULL,
    UNIQUE (document_id, ord)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
"""

_CHUNK_COLUMNS = (
    "c.ord, c.text, c.heading_path, c.page_start, c.page_end, c.start_s, c.end_s, c.speakers"
)


class KnowledgeStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        self._url = database_url
        self._dimensions = dimensions
        self._pool: ConnectionPool | None = None

    def open(self) -> None:
        """Create the schema if needed, check it, then open the connection pool."""
        with psycopg.connect(self._url, autocommit=True) as conn:
            conn.execute(_SCHEMA.format(dimensions=self._dimensions))
            (dimensions,) = conn.execute(
                "SELECT atttypmod FROM pg_attribute"
                " WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            ).fetchone()
        if dimensions != self._dimensions:
            raise RuntimeError(
                f"chunks.embedding stores {dimensions}-dimensional vectors but"
                f" EMBEDDING_DIMENSIONS is {self._dimensions}"
            )
        self._pool = ConnectionPool(
            self._url,
            min_size=1,
            max_size=8,
            configure=register_vector,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self._pool.wait()

    def close(self) -> None:
        if self._pool:
            self._pool.close()

    @property
    def pool(self) -> ConnectionPool:
        assert self._pool is not None, "KnowledgeStore.open() was not called"
        return self._pool

    def find(self, external_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE external_id = %s", (external_id,)
            ).fetchone()

    def update_details(
        self, document_id: UUID, collection: str, occurred_at: datetime, metadata: dict[str, str]
    ) -> dict[str, Any]:
        """Change what doesn't affect the chunks, without re-embedding."""
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE documents SET collection = %s, occurred_at = %s, metadata = %s,"
                " updated_at = now() WHERE id = %s RETURNING *",
                (collection, occurred_at, Jsonb(metadata), document_id),
            ).fetchone()

    def upsert(
        self,
        *,
        external_id: str,
        collection: str,
        title: str,
        source_type: str,
        occurred_at: datetime,
        metadata: dict[str, str],
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        embedding_model: str,
    ) -> tuple[dict[str, Any], bool]:
        """Insert or replace a document and all its chunks; returns (row, inserted)."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                INSERT INTO documents (external_id, collection, title, source_type, occurred_at,
                                       metadata, content_hash, chunk_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (external_id) DO UPDATE SET
                    collection = EXCLUDED.collection, title = EXCLUDED.title,
                    source_type = EXCLUDED.source_type, occurred_at = EXCLUDED.occurred_at,
                    metadata = EXCLUDED.metadata, content_hash = EXCLUDED.content_hash,
                    chunk_count = EXCLUDED.chunk_count, updated_at = now()
                RETURNING *, (xmax = 0) AS inserted
                """,
                (external_id, collection, title, source_type, occurred_at, Jsonb(metadata),
                 content_hash, len(chunks)),
            ).fetchone()  # fmt: skip
            conn.execute("DELETE FROM chunks WHERE document_id = %s", (row["id"],))
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks (document_id, ord, text, heading_path, page_start,
                                        page_end, start_s, end_s, speakers, tokens, embedding,
                                        embedding_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (row["id"], ord_, c.text, c.heading_path, c.page_start, c.page_end,
                         c.start_s, c.end_s, c.speakers, c.tokens, Vector(vector), embedding_model)
                        for ord_, (c, vector) in enumerate(zip(chunks, vectors, strict=True))
                    ],
                )  # fmt: skip
        return row, row.pop("inserted")

    def list_documents(
        self, *, collection: str | None, external_id: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                """
                SELECT * FROM documents
                WHERE (%(collection)s::text IS NULL OR collection = %(collection)s)
                  AND (%(external_id)s::text IS NULL OR external_id = %(external_id)s)
                ORDER BY occurred_at DESC, id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                {
                    "collection": collection,
                    "external_id": external_id,
                    "limit": limit,
                    "offset": offset,
                },
            ).fetchall()

    def get(self, document_id: UUID) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        with self.pool.connection() as conn:
            document = conn.execute(
                "SELECT * FROM documents WHERE id = %s", (document_id,)
            ).fetchone()
            if document is None:
                return None
            chunks = conn.execute(
                f"SELECT {_CHUNK_COLUMNS} FROM chunks c WHERE c.document_id = %s ORDER BY c.ord",
                (document_id,),
            ).fetchall()
        return document, chunks

    def delete(self, document_id: UUID) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM documents WHERE id = %s", (document_id,)).rowcount > 0

    def search(
        self, vector: list[float], request: SearchRequest, embedding_model: str
    ) -> list[dict[str, Any]]:
        """Nearest chunks by cosine distance, after filtering; each row carries its document."""
        filters = ["c.embedding_model = %(model)s"]
        params: dict[str, Any] = {"q": Vector(vector), "model": embedding_model, "k": request.top_k}
        if request.collections:
            filters.append("d.collection = ANY(%(collections)s)")
            params["collections"] = request.collections
        if request.document_ids:
            filters.append("d.id = ANY(%(document_ids)s)")
            params["document_ids"] = request.document_ids
        if request.occurred_after:
            filters.append("d.occurred_at >= %(after)s")
            params["after"] = request.occurred_after
        if request.occurred_before:
            filters.append("d.occurred_at < %(before)s")
            params["before"] = request.occurred_before
        if request.must_contain:
            filters.append("c.text ILIKE %(contains)s")
            params["contains"] = f"%{_escape_like(request.must_contain)}%"
        sql = f"""
            WITH hits AS MATERIALIZED (
                SELECT c.id, c.embedding <=> %(q)s AS distance
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE {" AND ".join(filters)}
                ORDER BY c.embedding <=> %(q)s
                LIMIT %(k)s
            )
            SELECT {_CHUNK_COLUMNS}, c.document_id, 1 - hits.distance AS score,
                   d.external_id, d.title, d.collection, d.occurred_at
            FROM hits JOIN chunks c ON c.id = hits.id JOIN documents d ON d.id = c.document_id
            ORDER BY hits.distance
        """
        with self.pool.connection() as conn, conn.transaction():
            # Keep scanning the index until enough rows pass the filters (pgvector >= 0.8).
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            return conn.execute(sql, params).fetchall()

    def neighbors(self, document_id: UUID, ord_: int, count: int) -> tuple[list[str], list[str]]:
        """Texts of up to `count` chunks before and after chunk `ord_`."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT ord, text FROM chunks WHERE document_id = %s"
                " AND ord BETWEEN %s AND %s AND ord <> %s ORDER BY ord",
                (document_id, ord_ - count, ord_ + count, ord_),
            ).fetchall()
        return (
            [r["text"] for r in rows if r["ord"] < ord_],
            [r["text"] for r in rows if r["ord"] > ord_],
        )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
