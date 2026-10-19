import asyncio
import subprocess
from pathlib import Path

import pytest

from transcriber.audio import (
    AudioError,
    Piece,
    analyze,
    parse_duration,
    parse_pauses,
    plan_pieces,
    source_of,
    split,
)


def test_short_recording_is_one_piece() -> None:
    assert plan_pieces(100, [50], target_s=180, search_s=45) == [Piece(0, 100)]


def test_cuts_at_latest_pause_before_target() -> None:
    pieces = plan_pieces(400, [100, 150, 170, 190, 330], target_s=180, search_s=45)
    assert pieces == [Piece(0, 170), Piece(170, 330), Piece(330, 400)]


def test_cuts_hard_without_a_nearby_pause() -> None:
    pieces = plan_pieces(400, [10], target_s=180, search_s=45)
    assert pieces == [Piece(0, 180), Piece(180, 360), Piece(360, 400)]


def test_tiny_tail_merges_into_previous_piece() -> None:
    assert plan_pieces(180.5, [], target_s=180, search_s=45) == [Piece(0, 180.5)]


def test_empty_recording_is_an_error() -> None:
    with pytest.raises(AudioError):
        plan_pieces(0, [], target_s=180, search_s=45)


def test_parse_ffmpeg_log() -> None:
    log = (
        "[silencedetect @ 0x1] silence_start: -0.01\n"
        "[silencedetect @ 0x1] silence_end: 1.5 | silence_duration: 1.51\n"
        "size=N/A time=00:00:05.00 bitrate=N/A\r"
        "[silencedetect @ 0x1] silence_start: 7\n"
        "[silencedetect @ 0x1] silence_end: 8 | silence_duration: 1\n"
        "size=N/A time=01:02:03.50 bitrate=N/A speed=99x\n"
    )
    assert parse_pauses(log) == [0.75, 7.5]
    assert parse_duration(log) == 3723.5


def test_source_of_joins_parts_without_copying() -> None:
    assert source_of([Path("/a/0")]) == "/a/0"
    assert source_of([Path("/a/0"), Path("/a/1")]) == "concat:/a/0|/a/1"


def test_analyze_and_split_real_audio(meeting_wav: Path, tmp_path: Path) -> None:
    # Analyze the file as three byte-range parts, the way uploads are stored.
    data = meeting_wav.read_bytes()
    third = len(data) // 3
    parts = []
    for i, chunk in enumerate([data[:third], data[third : 2 * third], data[2 * third :]]):
        part = tmp_path / f"part{i}"
        part.write_bytes(chunk)
        parts.append(part)
    source = source_of(parts)

    duration, pauses = asyncio.run(analyze(source))
    assert duration == pytest.approx(20, abs=0.1)
    assert pauses[:4] == pytest.approx([3.5, 7.5, 11.5, 15.5], abs=0.1)

    pieces = plan_pieces(duration, pauses, target_s=6, search_s=3)
    assert [round(p.end_s, 1) for p in pieces] == [3.5, 7.5, 11.5, 15.5, 20.0]

    out = tmp_path / "pieces"
    out.mkdir()
    files = asyncio.run(split(source, pieces, out))
    assert len(files) == 5
    durations = [_duration(f) for f in files]
    assert durations == pytest.approx([p.end_s - p.start_s for p in pieces], abs=0.15)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout)
