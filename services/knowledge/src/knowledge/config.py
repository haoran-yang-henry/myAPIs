from typing import Annotated, Literal, Self

from fastapi import Depends, Request
from pydantic import SecretStr, model_validator

from myapis_common import BaseServiceSettings, Env


class Settings(BaseServiceSettings):
    # Default matches the postgres service in docker-compose.yml.
    database_url: str = "postgresql://knowledge:knowledge@localhost:5434/knowledge"

    # "fake" hashes text locally instead of calling an API (tests, offline development).
    embedding_provider: Literal["openai", "fake"] = "openai"
    embedding_model: str = "text-embedding-3-large"
    # Fixed when the chunks table is created; pgvector indexes vectors up to 2000 dimensions.
    embedding_dimensions: int = 1536
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None

    # Chunk sizes, in estimated tokens.
    chunk_target_tokens: int = 500
    chunk_max_tokens: int = 800
    chunk_overlap_tokens: int = 60
    max_document_chars: int = 2_000_000

    @model_validator(mode="after")
    def _require_openai_key_outside_local(self) -> Self:
        if self.env is not Env.LOCAL and self.embedding_provider == "openai":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY must be set when ENV is not 'local'")
        return self


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]
