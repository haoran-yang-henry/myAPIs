"""Transcribe one audio file with the transcriber service and print the transcript.

    uv run python services/transcriber/scripts/transcribe_file.py meeting.m4a --language zh-cn

Env: TRANSCRIBER_URL (default http://localhost:8000) and MYAPIS_API_KEY.
This is also the reference for the client protocol: create, upload parts, complete, poll.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--language", action="append", dest="languages", help="e.g. zh-cn")
    parser.add_argument("--keyword", action="append", dest="keywords", help="name or term")
    parser.add_argument("--prompt", help="context about the recording")
    parser.add_argument("--json", action="store_true", help="print the whole transcript as JSON")
    args = parser.parse_args()

    url = os.environ.get("TRANSCRIBER_URL", "http://localhost:8000").rstrip("/")
    headers = {"X-API-Key": os.environ.get("MYAPIS_API_KEY", "")}
    with httpx2.Client(base_url=url, headers=headers, timeout=120) as client:
        recording = _check(
            client.post(
                "/v1/recordings",
                json={
                    "size_bytes": args.file.stat().st_size,
                    "sha256": _sha256(args.file),
                    "filename": args.file.name,
                    "languages": args.languages,
                    "keywords": args.keywords,
                    "prompt": args.prompt,
                },
            )
        )
        recording_id, part_size = recording["id"], recording["part_size_bytes"]
        with args.file.open("rb") as f:
            for index in range(recording["parts_total"]):
                f.seek(index * part_size)
                _check(
                    client.put(
                        f"/v1/recordings/{recording_id}/parts/{index}", content=f.read(part_size)
                    )
                )
                _log(f"uploaded part {index + 1}/{recording['parts_total']}")

        _check(client.post(f"/v1/recordings/{recording_id}/complete"))
        while (recording := _check(client.get(f"/v1/recordings/{recording_id}")))["status"] not in (
            "completed",
            "failed",
        ):
            progress = recording["progress"]
            _log(f"{recording['status']} {progress['pieces_done']}/{progress['pieces_total']}")
            time.sleep(3)
        if recording["status"] == "failed":
            sys.exit(f"transcription failed: {recording['error']}")
        transcript = _check(client.get(f"/v1/recordings/{recording_id}/transcript"))

    print(json.dumps(transcript, ensure_ascii=False, indent=2) if args.json else transcript["text"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _check(response: httpx2.Response) -> dict:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url.path}: {response.text}")
    return response.json() if response.content else {}


def _log(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    main()
