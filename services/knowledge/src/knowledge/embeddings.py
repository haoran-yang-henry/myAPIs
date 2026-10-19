import hashlib
import math
from typing import Protocol

import httpx2
from openai import OpenAI

from .config import Settings


class Embedder(Protocol):
    """One embedding provider. Vectors from different models are never compared."""

    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    _BATCH = 128

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self.model = model
        self._dimensions = dimensions
        # The SDK retries rate limits, 5xx responses and connection errors with backoff.
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, max_retries=3, timeout=120, http_client=http_client
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._BATCH):
            response = self._client.embeddings.create(
                model=self.model,
                input=texts[start : start + self._BATCH],
                dimensions=self._dimensions,
            )
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


class FakeEmbedder:
    """Offline stand-in: hashes characters and character pairs into a vector, so texts
    sharing words land near each other. Fine for tests and local development only."""

    def __init__(self, dimensions: int) -> None:
        self.model = f"fake-{dimensions}"
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text.lower()) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for feature in [*text, *(a + b for a, b in zip(text, text[1:], strict=False))]:
            if feature.strip():
                digest = hashlib.blake2b(feature.encode(), digest_size=4).digest()
                vector[int.from_bytes(digest, "little") % self._dimensions] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


def embedder_from(settings: Settings) -> Embedder | None:
    """The configured provider, or None when it has no credentials."""
    if settings.embedding_provider == "fake":
        return FakeEmbedder(settings.embedding_dimensions)
    if settings.openai_api_key is None:
        return None
    return OpenAIEmbedder(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        base_url=settings.openai_base_url,
    )
