from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx2
from openai import AsyncOpenAI, omit

from .config import Settings


@dataclass(frozen=True)
class AsrResult:
    text: str
    languages: list[str]


class SpeechToText(Protocol):
    """One speech-to-text provider. Add a provider by implementing this, not by changing callers."""

    model: str

    async def transcribe(
        self, audio: Path, *, languages: list[str], keywords: list[str], prompt: str | None
    ) -> AsrResult: ...


class OpenAISpeechToText:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.model = model
        # The SDK retries rate limits, 5xx responses and connection errors with backoff.
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=3, timeout=300, http_client=http_client
        )

    async def transcribe(
        self, audio: Path, *, languages: list[str], keywords: list[str], prompt: str | None
    ) -> AsrResult:
        result = await self._client.audio.transcriptions.create(
            model=self.model,
            file=(audio.name, audio.read_bytes(), "audio/mpeg"),
            languages=languages or omit,
            keywords=keywords or omit,
            prompt=prompt or omit,
        )
        return AsrResult(
            text=result.text.strip(),
            languages=[language.code for language in result.languages or []],
        )


class FakeSpeechToText:
    """Offline stand-in that returns placeholder text, for building callers without an API key."""

    model = "fake"

    async def transcribe(
        self, audio: Path, *, languages: list[str], keywords: list[str], prompt: str | None
    ) -> AsrResult:
        return AsrResult(text=f"[fake transcript of {audio.name}]", languages=languages[:1])


def speech_to_text_from(settings: Settings) -> SpeechToText | None:
    """The configured provider, or None when it has no credentials."""
    if settings.asr_provider == "fake":
        return FakeSpeechToText()
    if settings.openai_api_key is None:
        return None
    return OpenAISpeechToText(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.asr_model,
        base_url=settings.openai_base_url,
    )
