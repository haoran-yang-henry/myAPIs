"""Add files to the knowledge service and search it from the command line.

    uv run python services/knowledge/scripts/kb.py add notes.md --collection docs
    uv run python services/knowledge/scripts/kb.py add transcript.json   # from myRecorder
    uv run python services/knowledge/scripts/kb.py search "budget owner" --collection meetings

Env: KNOWLEDGE_URL (default http://localhost:8003) and MYAPIS_API_KEY.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add or update a Markdown/text file or a transcript")
    add.add_argument("file", type=Path)
    add.add_argument("--collection", help="default: meetings for transcripts, docs otherwise")
    add.add_argument("--title")
    find = commands.add_parser("search", help="find passages")
    find.add_argument("query")
    find.add_argument("--collection", action="append", dest="collections")
    find.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    url = os.environ.get("KNOWLEDGE_URL", "http://localhost:8003").rstrip("/")
    headers = {"X-API-Key": os.environ.get("MYAPIS_API_KEY", "")}
    with httpx2.Client(base_url=url, headers=headers, timeout=300) as client:
        if args.command == "add":
            result = _check(client.post("/v1/documents", json=_document(args)))
            document = result["document"]
            print(f"{result['status']}: {document['title']} ({document['chunk_count']} chunks)")
        else:
            body = {"query": args.query, "collections": args.collections, "top_k": args.top_k}
            for hit in _check(client.post("/v1/search", json=body))["hits"]:
                print(f"\n[{hit['score']:.3f}] {hit['document']['title']}{_where(hit['chunk'])}")
                print(hit["chunk"]["text"])


def _document(args: argparse.Namespace) -> dict:
    path: Path = args.file
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":  # a transcript, as saved by myRecorder or the transcriber
        transcript = json.loads(text)
        started_at = transcript.get("metadata", {}).get("started_at")
        return {
            "external_id": f"transcriber:{transcript['recording_id']}",
            "collection": args.collection or "meetings",
            "title": args.title or f"Recording {(started_at or '')[:16].replace('T', ' ')}".strip(),
            "occurred_at": started_at,
            "metadata": transcript.get("metadata", {}),
            "content": {"format": "transcript", "segments": transcript["segments"]},
        }
    headings = (line[2:].strip() for line in text.splitlines() if line.startswith("# "))
    first_heading = next(headings, None)
    return {
        "external_id": f"file:{path.resolve()}",
        "collection": args.collection or "docs",
        "title": args.title or first_heading or path.stem,
        "content": {"format": "markdown", "text": text},
    }


def _where(chunk: dict) -> str:
    parts = []
    if chunk["heading_path"]:
        parts.append(" > ".join(chunk["heading_path"]))
    if chunk["start_s"] is not None:
        minutes, seconds = divmod(int(chunk["start_s"]), 60)
        parts.append(f"{minutes:02d}:{seconds:02d}")
    return f" | {' | '.join(parts)}" if parts else ""


def _check(response: httpx2.Response) -> dict:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url.path}: {response.text}")
    return response.json()


if __name__ == "__main__":
    main()
