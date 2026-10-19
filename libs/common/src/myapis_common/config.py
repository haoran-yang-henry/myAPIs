from enum import StrEnum
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(StrEnum):
    LOCAL = "local"
    PROD = "prod"


class BaseServiceSettings(BaseSettings):
    """Settings every service shares. Subclass it to add service-specific fields.

    Values come from environment variables (case-insensitive), then from `.env`
    in the working directory.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Env = Env.LOCAL
    log_level: str = "INFO"
    # Keys accepted in the X-API-Key header, comma-separated. Each entry is `name:key`
    # (one per caller, e.g. recorder:abc123, so logs show who called and a leaked key
    # can be revoked alone) or a bare key.
    api_keys: str = ""

    @property
    def api_key_callers(self) -> dict[str, str]:
        """Accepted keys, each mapped to the name of the caller that holds it."""
        callers = {}
        entries = [entry.strip() for entry in self.api_keys.split(",") if entry.strip()]
        for number, entry in enumerate(entries, start=1):
            name, separator, key = entry.partition(":")
            if separator:
                callers[key.strip()] = name.strip()
            else:
                callers[entry] = f"key{number}"
        return callers

    @model_validator(mode="after")
    def _require_keys_outside_local(self) -> Self:
        if self.env is not Env.LOCAL and not self.api_key_callers:
            raise ValueError("API_KEYS must be set when ENV is not 'local'")
        return self
