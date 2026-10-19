from pathlib import Path
from typing import Annotated, Literal, Self

from fastapi import Depends, Request
from pydantic import SecretStr, model_validator

from myapis_common import BaseServiceSettings, Env


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseServiceSettings):
    # "fake" returns placeholder text without calling any API (offline development).
    asr_provider: Literal["openai", "fake"] = "openai"
    asr_model: str = "gpt-transcribe"
    openai_api_key: SecretStr | None = None
    # Point at an OpenAI-compatible provider instead of api.openai.com.
    openai_base_url: str | None = None
    # Defaults for recordings that don't set their own (comma-separated), e.g. "zh-cn,en".
    asr_languages: str = ""
    asr_keywords: str = ""

    # Recordings, upload parts and transcripts live here; a Cloud Storage mount in prod.
    data_dir: Path = Path(".data/transcriber")
    # Cloud Run caps HTTP/1 request bodies at 32 MiB, so uploads arrive in parts.
    part_size_bytes: int = 16 * 1024 * 1024
    max_recording_bytes: int = 2 * 1024 * 1024 * 1024
    # Recordings are cut at pauses into pieces of about this length, one API call each.
    piece_seconds: float = 180
    # How far before each target cut point to look for a pause.
    piece_search_seconds: float = 45
    transcribe_concurrency: int = 4
    # A queued/transcribing job with no progress for this long counts as dead and can be restarted.
    stale_job_seconds: int = 900

    @property
    def default_languages(self) -> list[str]:
        return _split_csv(self.asr_languages)

    @property
    def default_keywords(self) -> list[str]:
        return _split_csv(self.asr_keywords)

    @model_validator(mode="after")
    def _require_openai_key_outside_local(self) -> Self:
        if self.env is not Env.LOCAL and self.asr_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY must be set when ENV is not 'local'")
        return self


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]
