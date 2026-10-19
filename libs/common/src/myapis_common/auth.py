import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from myapis_common.errors import ApiError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    request: Request, api_key: Annotated[str | None, Depends(_api_key_header)]
) -> None:
    """Reject the request unless X-API-Key matches one of the configured keys, and record
    which caller it belongs to in request.state.caller (it shows up in the request log).

    With no keys configured (settings only allow that in the local env), every request passes.
    """
    callers = request.app.state.settings.api_key_callers
    if not callers:
        return
    if api_key is not None:
        for key, name in callers.items():
            if secrets.compare_digest(api_key.encode(), key.encode()):
                request.state.caller = name
                return
    raise ApiError(401, "unauthorized", "Missing or invalid X-API-Key header")
