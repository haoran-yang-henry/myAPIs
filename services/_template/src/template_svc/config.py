from typing import Annotated

from fastapi import Depends, Request

from myapis_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    # Service-specific settings, read from env vars of the same name (e.g. GREETING).
    greeting: str = "hello"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]
