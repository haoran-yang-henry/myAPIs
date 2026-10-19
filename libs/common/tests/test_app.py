import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from myapis_common import ApiError, BaseServiceSettings, create_app

router = APIRouter()


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, int]:
    return {"id": item_id}


@router.get("/teapot")
def teapot() -> None:
    raise ApiError(418, "teapot", "I'm a teapot")


@router.get("/conflict")
def conflict() -> None:
    raise ApiError(409, "conflict", "Not yet", details={"missing": [1, 2]})


@router.get("/boom")
def boom() -> None:
    raise RuntimeError("kaboom")


def make_client(**settings: str) -> TestClient:
    app = create_app(
        name="test",
        version="0",
        settings=BaseServiceSettings(_env_file=None, **settings),
        routers=[router],
    )
    return TestClient(app)


client = make_client(api_keys="k1, k2")
AUTH = {"X-API-Key": "k2"}


def test_health_is_public() -> None:
    assert client.get("/health").json() == {"status": "ok", "service": "test", "version": "0"}


def test_v1_rejects_missing_or_wrong_key() -> None:
    assert client.get("/v1/items/1").status_code == 401
    resp = client.get("/v1/items/1", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_v1_accepts_any_configured_key() -> None:
    assert client.get("/v1/items/1", headers=AUTH).json() == {"id": 1}


def test_named_keys_identify_the_caller(caplog: pytest.LogCaptureFixture) -> None:
    named = make_client(api_keys="recorder:rec-secret, mcp:mcp-secret")
    with caplog.at_level("INFO", logger="myapis.request"):
        assert named.get("/v1/items/1", headers={"X-API-Key": "mcp-secret"}).status_code == 200
    record = [r for r in caplog.records if r.name == "myapis.request"][-1]
    assert record.fields["caller"] == "mcp"
    assert record.getMessage().endswith("by mcp")
    # The name is not part of the key.
    assert named.get("/v1/items/1", headers={"X-API-Key": "mcp:mcp-secret"}).status_code == 401


def test_local_env_without_keys_allows_all() -> None:
    assert make_client().get("/v1/items/1").status_code == 200


def test_non_local_env_requires_keys() -> None:
    with pytest.raises(ValueError, match="API_KEYS"):
        BaseServiceSettings(_env_file=None, env="prod")


def test_api_error_shape_and_request_id_echo() -> None:
    resp = client.get("/v1/teapot", headers={**AUTH, "X-Request-ID": "abc-123"})
    assert resp.status_code == 418
    assert resp.headers["X-Request-ID"] == "abc-123"
    assert resp.json() == {
        "error": {"code": "teapot", "message": "I'm a teapot", "request_id": "abc-123"}
    }


def test_api_error_details_are_included() -> None:
    error = client.get("/v1/conflict", headers=AUTH).json()["error"]
    assert error["details"] == {"missing": [1, 2]}


def test_invalid_request_id_is_replaced() -> None:
    resp = client.get("/health", headers={"X-Request-ID": "x" * 200})
    assert resp.headers["X-Request-ID"] != "x" * 200


def test_unhandled_exception_returns_json_500() -> None:
    resp = client.get("/v1/boom", headers=AUTH)
    assert resp.status_code == 500
    body = resp.json()["error"]
    assert body["code"] == "internal_error"
    assert body["request_id"] == resp.headers["X-Request-ID"]


def test_not_found_and_validation_errors_use_same_shape() -> None:
    assert client.get("/nope").json()["error"]["code"] == "not_found"
    resp = client.get("/v1/items/abc", headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
