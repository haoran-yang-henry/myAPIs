from importlib.metadata import version

from fastapi import FastAPI

from myapis_common import create_app

from .config import Settings
from .routes import router


def build_app(settings: Settings | None = None) -> FastAPI:
    return create_app(
        name="template-svc",
        version=version("template-svc"),
        settings=settings or Settings(),
        routers=[router],
    )


app = build_app()
