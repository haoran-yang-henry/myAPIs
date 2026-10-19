import hashlib
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transcriber.asr import AsrResult
from transcriber.config import Settings
from transcriber.main import build_app

AUTH = {"X-API-Key": "test-key"}


class RecordingAsr:
    """Fake provider that names each piece and records what it was asked."""

    model = "fake-model"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    async def transcribe(self, audio, *, languages, keywords, prompt) -> AsrResult:
        if self.fail:
            raise RuntimeError("provider down")
        self.calls.append({"languages": languages, "keywords": keywords, "prompt": prompt})
        index = int(audio.stem.removeprefix("piece_"))
        return AsrResult(text=f"第{index}段", languages=["zh"])


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "api_keys": "test-key",
        "data_dir": tmp_path / "data",
        "part_size_bytes": 100_000,
        "piece_seconds": 6,
        "piece_search_seconds": 3,
        "asr_languages": "zh-cn,en",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def upload(client: TestClient, data: bytes, **create_fields) -> dict:
    created = client.post(
        "/v1/recordings", json={"size_bytes": len(data), **create_fields}, headers=AUTH
    )
    assert created.status_code == 201, created.text
    recording = created.json()
    size = recording["part_size_bytes"]
    indexes = list(range(recording["parts_total"]))
    random.shuffle(indexes)  # parts may arrive in any order
    for i in indexes:
        resp = client.put(
            f"/v1/recordings/{recording['id']}/parts/{i}",
            content=data[i * size : (i + 1) * size],
            headers=AUTH,
        )
        assert resp.status_code == 204, resp.text
    return recording


def wait_until_done(client: TestClient, recording_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        recording = client.get(f"/v1/recordings/{recording_id}", headers=AUTH).json()
        if recording["status"] in ("completed", "failed"):
            return recording
        time.sleep(0.05)
    pytest.fail("transcription did not finish")


def test_full_recording_flow(tmp_path: Path, meeting_wav: Path) -> None:
    data = meeting_wav.read_bytes()
    asr = RecordingAsr()
    with TestClient(build_app(make_settings(tmp_path), asr=asr)) as client:
        recording = upload(
            client,
            data,
            filename="weekly.wav",
            sha256=hashlib.sha256(data).hexdigest(),
            keywords=["Kubernetes"],
            prompt="周会",
            metadata={"title": "周会"},
        )
        assert recording["parts_total"] == 7

        resp = client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
        assert resp.status_code == 202
        done = wait_until_done(client, recording["id"])
        assert done["status"] == "completed", done["error"]
        assert done["progress"] == {"pieces_done": 5, "pieces_total": 5}

        transcript = client.get(f"/v1/recordings/{recording['id']}/transcript", headers=AUTH)
        body = transcript.json()

    assert body["text"] == "第0段\n第1段\n第2段\n第3段\n第4段"
    assert [round(s["end_s"], 1) for s in body["segments"]] == [3.5, 7.5, 11.5, 15.5, 20.0]
    assert body["languages"] == ["zh"]
    assert body["metadata"] == {"title": "周会"}
    assert asr.calls[0] == {
        "languages": ["zh-cn", "en"],
        "keywords": ["Kubernetes"],
        "prompt": "周会",
    }
    # Audio is gone once transcribed; only metadata and the transcript remain.
    remaining = {p.name for p in (tmp_path / "data" / recording["id"]).iterdir()}
    assert remaining == {"recording.json", "transcript.json"}


def test_failed_job_keeps_audio_and_can_be_retried(tmp_path: Path, meeting_wav: Path) -> None:
    data = meeting_wav.read_bytes()
    asr = RecordingAsr(fail=True)
    with TestClient(build_app(make_settings(tmp_path), asr=asr)) as client:
        recording = upload(client, data)
        client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
        failed = wait_until_done(client, recording["id"])
        assert failed["status"] == "failed"
        assert "provider down" in failed["error"]

        asr.fail = False
        resp = client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
        assert resp.status_code == 202
        assert wait_until_done(client, recording["id"])["status"] == "completed"


def test_complete_reports_missing_parts(tmp_path: Path) -> None:
    with TestClient(build_app(make_settings(tmp_path), asr=RecordingAsr())) as client:
        recording = client.post("/v1/recordings", json={"size_bytes": 250_000}, headers=AUTH).json()
        client.put(
            f"/v1/recordings/{recording['id']}/parts/1", content=b"x" * 100_000, headers=AUTH
        )
        resp = client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"missing_parts": [0, 2]}


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    with TestClient(build_app(make_settings(tmp_path), asr=RecordingAsr())) as client:
        recording = upload(client, b"y" * 150_000, sha256="0" * 64)
        resp = client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
        assert resp.status_code == 422
        # Still uploading, so the parts can be sent again.
        assert client.get(f"/v1/recordings/{recording['id']}", headers=AUTH).json()["status"] == (
            "uploading"
        )


def test_part_validation(tmp_path: Path) -> None:
    with TestClient(build_app(make_settings(tmp_path), asr=RecordingAsr())) as client:
        recording = client.post("/v1/recordings", json={"size_bytes": 150_000}, headers=AUTH).json()
        base = f"/v1/recordings/{recording['id']}/parts"
        assert client.put(f"{base}/0", content=b"x" * 10, headers=AUTH).status_code == 400
        assert client.put(f"{base}/0", content=b"x" * 100_001, headers=AUTH).status_code == 400
        assert client.put(f"{base}/2", content=b"x", headers=AUTH).status_code == 400
        assert client.put(f"{base}/1", content=b"x" * 50_000, headers=AUTH).status_code == 204
        assert client.get("/v1/recordings/" + "0" * 32, headers=AUTH).status_code == 404
        assert client.get("/v1/recordings/not-an-id", headers=AUTH).status_code == 422


def test_rejects_oversized_recording(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_recording_bytes=1000)
    with TestClient(build_app(settings, asr=RecordingAsr())) as client:
        resp = client.post("/v1/recordings", json={"size_bytes": 1001}, headers=AUTH)
    assert resp.status_code == 413


def test_complete_without_credentials_is_503(tmp_path: Path) -> None:
    with TestClient(build_app(make_settings(tmp_path))) as client:
        recording = client.post("/v1/recordings", json={"size_bytes": 10}, headers=AUTH).json()
        resp = client.post(f"/v1/recordings/{recording['id']}/complete", headers=AUTH)
    assert resp.status_code == 503


def test_transcript_not_ready_and_delete(tmp_path: Path) -> None:
    with TestClient(build_app(make_settings(tmp_path), asr=RecordingAsr())) as client:
        recording = client.post("/v1/recordings", json={"size_bytes": 10}, headers=AUTH).json()
        url = f"/v1/recordings/{recording['id']}"
        assert client.get(f"{url}/transcript", headers=AUTH).status_code == 409
        assert client.delete(url, headers=AUTH).status_code == 204
        assert client.get(url, headers=AUTH).status_code == 404


def test_prod_requires_openai_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, env="prod", api_keys="k")
