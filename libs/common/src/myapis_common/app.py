import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager

from fastapi import APIRouter, Depends, FastAPI, Request, Response

from myapis_common.auth import require_api_key
from myapis_common.config import BaseServiceSettings, Env
from myapis_common.errors import error_response, install_error_handlers
from myapis_common.log import request_id_var, setup_logging

logger = logging.getLogger("myapis.request")

_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


async def _request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Tag each request with an ID (reusing the caller's X-Request-ID) and log one line for it."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error")
            response = error_response(500, "internal_error", "Internal server error")
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - start) * 1000)
        caller = getattr(request.state, "caller", None)  # set by require_api_key
        logger.info(
            "%s %s -> %d (%d ms)%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            f" by {caller}" if caller else "",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "caller": caller,
                }
            },
        )
        return response
    finally:
        request_id_var.reset(token)


def create_app(
    *,
    name: str,
    version: str,
    settings: BaseServiceSettings,
    routers: Sequence[APIRouter] = (),
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Build a FastAPI app with the shared conventions wired in.

    Every router is mounted under /v1 behind the API key check; /health stays public.
    `lifespan` runs around the app's life, e.g. to open and close a database pool.
    """
    setup_logging(settings.log_level, json_format=settings.env is not Env.LOCAL)

    app = FastAPI(title=name, version=version, lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.middleware("http")(_request_context)

    # Not /healthz: Cloud Run reserves paths ending in "z".
    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": name, "version": version}

    for router in routers:
        app.include_router(router, prefix="/v1", dependencies=[Depends(require_api_key)])
    return app
