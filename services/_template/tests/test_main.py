from fastapi.testclient import TestClient

from template_svc.config import Settings
from template_svc.main import build_app

client = TestClient(build_app(Settings(_env_file=None, api_keys="test-key")))
AUTH = {"X-API-Key": "test-key"}


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_echo_requires_api_key() -> None:
    assert client.post("/v1/echo", json={"message": "world"}).status_code == 401


def test_echo() -> None:
    resp = client.post("/v1/echo", json={"message": "world"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"reply": "hello, world"}
