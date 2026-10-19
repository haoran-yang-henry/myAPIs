import base64
import json
import struct

import httpx2

from knowledge.embeddings import FakeEmbedder, OpenAIEmbedder


def test_openai_batches_and_keeps_order() -> None:
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        # Answer out of order: the client must sort by index. The SDK asks for base64.
        data = [
            {"object": "embedding", "index": i, "embedding": _b64([float(len(text)), 0.0])}
            for i, text in reversed(list(enumerate(body["input"])))
        ]
        return httpx2.Response(
            200,
            json={"object": "list", "data": data, "model": body["model"],
                  "usage": {"prompt_tokens": 1, "total_tokens": 1}},
        )  # fmt: skip

    embedder = OpenAIEmbedder(
        api_key="sk-test",
        model="text-embedding-3-large",
        dimensions=2,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    texts = ["x" * n for n in range(1, 131)]

    vectors = embedder.embed(texts)

    assert [len(r["input"]) for r in requests] == [128, 2]
    assert requests[0]["model"] == "text-embedding-3-large"
    assert requests[0]["dimensions"] == 2
    assert [v[0] for v in vectors] == [float(n) for n in range(1, 131)]


def test_fake_embedder_puts_similar_texts_closer() -> None:
    embedder = FakeEmbedder(256)
    budget, budget2, hiring = embedder.embed(["讨论预算安排", "预算由谁负责", "招聘新同事"])

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(budget, budget2) > cosine(budget, hiring)


def _b64(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()
