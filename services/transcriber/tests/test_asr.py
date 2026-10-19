import asyncio
from pathlib import Path

import httpx2

from transcriber.asr import AsrResult, OpenAISpeechToText


def test_openai_request_and_response(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx2.Response(200, json={"text": " 大家好 ", "languages": [{"code": "zh"}]})

    asr = OpenAISpeechToText(
        api_key="sk-test",
        model="gpt-transcribe",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    piece = tmp_path / "piece_0000.mp3"
    piece.write_bytes(b"ID3 not really audio")

    result = asyncio.run(
        asr.transcribe(piece, languages=["zh-cn", "en"], keywords=["Kubernetes"], prompt="周会")
    )

    assert result == AsrResult(text="大家好", languages=["zh"])
    assert str(seen["url"]).endswith("/audio/transcriptions")
    body = seen["body"]
    assert isinstance(body, bytes)
    for expected in (
        b'name="model"\r\n\r\ngpt-transcribe',
        b'name="languages[]"\r\n\r\nzh-cn',
        b'name="languages[]"\r\n\r\nen',
        b'name="keywords[]"\r\n\r\nKubernetes',
        'name="prompt"\r\n\r\n周会'.encode(),
        b'filename="piece_0000.mp3"',
    ):
        assert expected in body, expected
