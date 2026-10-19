import asyncio
from importlib.metadata import version

from fastapi import FastAPI

from myapis_common import create_app

from .asr import SpeechToText, speech_to_text_from
from .config import Settings
from .pipeline import JobRunner
from .routes import router
from .store import RecordingStore


def build_app(settings: Settings | None = None, asr: SpeechToText | None = None) -> FastAPI:
    settings = settings or Settings()
    app = create_app(
        name="transcriber",
        version=version("transcriber"),
        settings=settings,
        routers=[router],
    )
    app.state.store = RecordingStore(settings.data_dir)
    app.state.asr = asr or speech_to_text_from(settings)
    app.state.jobs = JobRunner()
    app.state.complete_lock = asyncio.Lock()
    return app


app = build_app()
