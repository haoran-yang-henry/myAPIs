from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from knowledge.config import Settings
from knowledge.embeddings import FakeEmbedder
from knowledge.main import build_app

AUTH = {"X-API-Key": "test-key"}

PLAN = """# 项目计划

## 预算

总预算十万元，由王负责审批预算。

## 招聘

下个月招聘两名新同事。
"""


class CountingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__(64)
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed(texts)


def make_settings(database_url: str, **overrides) -> Settings:
    values = {
        "api_keys": "test-key",
        "database_url": database_url,
        "embedding_provider": "fake",
        "embedding_dimensions": 64,
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture
def client(database_url: str, embedder: CountingEmbedder) -> Iterator[TestClient]:
    with TestClient(build_app(make_settings(database_url), embedder=embedder)) as client:
        yield client


def ingest(client: TestClient, **fields) -> dict:
    body = {
        "external_id": "file:plan.md",
        "collection": "docs",
        "title": "项目计划",
        "content": {"format": "markdown", "text": PLAN},
        **fields,
    }
    resp = client.post("/v1/documents", json=body, headers=AUTH)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def search(client: TestClient, query: str, **fields) -> list[dict]:
    resp = client.post("/v1/search", json={"query": query, **fields}, headers=AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()["hits"]


def test_ingest_markdown_and_read_back(client: TestClient) -> None:
    result = ingest(client)
    assert result["status"] == "created"
    document = result["document"]
    assert (document["source_type"], document["chunk_count"]) == ("markdown", 2)

    detail = client.get(f"/v1/documents/{document['id']}", headers=AUTH).json()
    assert [(c["heading_path"], c["text"]) for c in detail["chunks"]] == [
        (["项目计划", "预算"], "总预算十万元，由王负责审批预算。"),
        (["项目计划", "招聘"], "下个月招聘两名新同事。"),
    ]


def test_same_content_is_not_embedded_again(client: TestClient, embedder) -> None:
    ingest(client)
    calls = embedder.calls
    again = ingest(client, metadata={"author": "王"})
    assert again["status"] == "unchanged"
    assert again["document"]["metadata"] == {"author": "王"}  # details still update
    assert embedder.calls == calls


def test_changed_content_replaces_chunks(client: TestClient) -> None:
    first = ingest(client)["document"]
    changed = ingest(client, content={"format": "markdown", "text": "# 新计划\n\n只剩一段。"})
    assert changed["status"] == "updated"
    assert changed["document"]["id"] == first["id"]
    detail = client.get(f"/v1/documents/{first['id']}", headers=AUTH).json()
    assert [c["text"] for c in detail["chunks"]] == ["只剩一段。"]


def test_transcript_keeps_times(client: TestClient) -> None:
    result = ingest(
        client,
        external_id="transcriber:abc",
        collection="meetings",
        title="周会",
        occurred_at="2026-10-19T15:40:00+02:00",
        content={
            "format": "transcript",
            "segments": [{"start_s": 0, "end_s": 27.5, "text": "大家好，今天讨论预算。"}],
        },
    )
    assert result["document"]["occurred_at"].startswith("2026-10-19T13:40:00")
    (hit,) = search(client, "预算", collections=["meetings"])
    assert (hit["chunk"]["start_s"], hit["chunk"]["end_s"]) == (0, 27.5)
    assert hit["document"]["title"] == "周会"


def test_search_ranks_and_filters(client: TestClient) -> None:
    ingest(client)
    ingest(
        client,
        external_id="transcriber:abc",
        collection="meetings",
        title="周会",
        occurred_at="2026-10-19T10:00:00Z",
        content={
            "format": "transcript",
            "segments": [{"start_s": 0, "end_s": 10, "text": "招聘的事下周再说。"}],
        },
    )

    hits = search(client, "预算审批")
    assert hits[0]["chunk"]["text"] == "总预算十万元，由王负责审批预算。"
    assert hits[0]["score"] > hits[-1]["score"]

    assert {
        h["document"]["collection"] for h in search(client, "招聘", collections=["meetings"])
    } == {"meetings"}
    assert [h["chunk"]["text"] for h in search(client, "负责人", must_contain="王")] == [
        "总预算十万元，由王负责审批预算。"
    ]
    assert (
        search(client, "招聘", occurred_after="2026-10-20T00:00:00Z", collections=["meetings"])
        == []
    )


def test_search_returns_neighbors(client: TestClient) -> None:
    ingest(client)
    (hit,) = search(client, "招聘新同事", top_k=1, neighbors=1)
    assert hit["chunk"]["text"] == "下个月招聘两名新同事。"
    assert hit["context_before"] == "总预算十万元，由王负责审批预算。"
    assert hit["context_after"] == ""


def test_list_and_delete(client: TestClient) -> None:
    document = ingest(client)["document"]
    listed = client.get("/v1/documents", params={"collection": "docs"}, headers=AUTH).json()
    assert [d["id"] for d in listed] == [document["id"]]
    assert client.get("/v1/documents", params={"collection": "none"}, headers=AUTH).json() == []

    assert client.delete(f"/v1/documents/{document['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/v1/documents/{document['id']}", headers=AUTH).status_code == 404
    assert search(client, "预算") == []


def test_empty_document_is_rejected(client: TestClient) -> None:
    resp = client.post(
        "/v1/documents",
        json={"external_id": "x", "title": "空", "content": {"format": "markdown", "text": "\n"}},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_without_embedding_credentials(database_url: str) -> None:
    settings = make_settings(database_url, embedding_provider="openai")
    with TestClient(build_app(settings)) as client:
        resp = client.post("/v1/search", json={"query": "预算"}, headers=AUTH)
    assert resp.status_code == 503


def test_dimension_change_is_refused(database_url: str) -> None:
    with TestClient(build_app(make_settings(database_url))):
        pass  # creates the table with 64 dimensions
    with pytest.raises(RuntimeError, match="64-dimensional"):
        with TestClient(build_app(make_settings(database_url, embedding_dimensions=32))):
            pass
