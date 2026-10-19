import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from myapis_common import create_app

from .config import Settings
from .embeddings import Embedder, embedder_from
from .routes import router
from .store import KnowledgeStore


def build_app(settings: Settings | None = None, embedder: Embedder | None = None) -> FastAPI:
    settings = settings or Settings()
    store = KnowledgeStore(settings.database_url, settings.embedding_dimensions)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(store.open)
        try:
            yield
        finally:
            await asyncio.to_thread(store.close)

    app = create_app(
        name="knowledge",
        version=version("knowledge"),
        settings=settings,
        routers=[router],
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.embedder = embedder or embedder_from(settings)
    return app


app = build_app()
