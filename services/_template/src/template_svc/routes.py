from fastapi import APIRouter
from pydantic import BaseModel, Field

from .config import SettingsDep

router = APIRouter(tags=["template-svc"])


class EchoRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class EchoResponse(BaseModel):
    reply: str


@router.post("/echo")
def echo(body: EchoRequest, settings: SettingsDep) -> EchoResponse:
    """Example endpoint; replace with the service's real routes."""
    return EchoResponse(reply=f"{settings.greeting}, {body.message}")
