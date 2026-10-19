import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

# A pause is at least this long and this quiet.
_SILENCE_NOISE = "-35dB"
_SILENCE_MIN_SECONDS = 0.4
# A leftover tail shorter than this is merged into the piece before it.
_MIN_PIECE_SECONDS = 1.0
# What each piece is re-encoded to before upload: small, and accepted by every ASR API.
_PIECE_ENCODING = ("-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k")

_SILENCE = re.compile(r"silence_(start|end): (-?\d+(?:\.\d+)?)")
_PROGRESS_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class AudioError(Exception):
    """ffmpeg could not read or convert the recording."""


@dataclass(frozen=True)
class Piece:
    start_s: float
    end_s: float


def source_of(parts: Sequence[Path]) -> str:
    """ffmpeg input for a file stored as consecutive byte-range parts, read without joining them."""
    if len(parts) == 1:
        return str(parts[0])
    return "concat:" + "|".join(str(part) for part in parts)


def parse_pauses(ffmpeg_log: str) -> list[float]:
    """Midpoints of the pauses that silencedetect reported."""
    pauses: list[float] = []
    start: float | None = None
    for kind, value in _SILENCE.findall(ffmpeg_log):
        if kind == "start":
            start = max(float(value), 0.0)
        elif start is not None:
            pauses.append((start + float(value)) / 2)
            start = None
    return pauses


def parse_duration(ffmpeg_log: str) -> float:
    """Duration of the decoded audio, from ffmpeg's last progress line."""
    matches = _PROGRESS_TIME.findall(ffmpeg_log)
    if not matches:
        raise AudioError("Could not determine the recording's duration")
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def plan_pieces(
    duration_s: float, pauses: Sequence[float], target_s: float, search_s: float
) -> list[Piece]:
    """Cut [0, duration_s] into pieces of at most target_s, preferring to cut inside a pause.

    Each cut goes at the latest pause within search_s before the target point;
    with no pause there, it cuts hard at the target point.
    """
    if duration_s <= 0:
        raise AudioError("The recording contains no audio")
    cuts: list[float] = []
    start = 0.0
    while duration_s - start > target_s:
        target = start + target_s
        nearby = [p for p in pauses if max(start, target - search_s) < p <= target]
        start = max(nearby) if nearby else target
        cuts.append(start)
    if cuts and duration_s - cuts[-1] < _MIN_PIECE_SECONDS:
        cuts.pop()
    return [Piece(a, b) for a, b in pairwise([0.0, *cuts, duration_s])]


async def analyze(source: str) -> tuple[float, list[float]]:
    """Decode the whole recording once; return its duration and the midpoints of its pauses."""
    detect = f"silencedetect=noise={_SILENCE_NOISE}:d={_SILENCE_MIN_SECONDS}"
    log = await _ffmpeg("-i", source, "-map", "0:a:0", "-af", detect, "-f", "null", "-")
    return parse_duration(log), parse_pauses(log)


async def split(source: str, pieces: Sequence[Piece], out_dir: Path) -> list[Path]:
    """Re-encode the recording into one small mono MP3 per piece, in a single decoding pass."""
    if len(pieces) == 1:
        output = out_dir / "piece_0000.mp3"
        await _ffmpeg("-i", source, *_PIECE_ENCODING, str(output))
        return [output]
    cut_times = ",".join(f"{piece.start_s:.3f}" for piece in pieces[1:])
    await _ffmpeg(
        "-i", source, *_PIECE_ENCODING,
        "-f", "segment", "-segment_times", cut_times, "-reset_timestamps", "1",
        str(out_dir / "piece_%04d.mp3"),
    )  # fmt: skip
    outputs = sorted(out_dir.glob("piece_*.mp3"))
    if len(outputs) != len(pieces):
        raise AudioError(f"Expected {len(pieces)} pieces but ffmpeg produced {len(outputs)}")
    return outputs


async def _ffmpeg(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-stats", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, stderr = await process.communicate()
    log = stderr.decode(errors="replace")
    if process.returncode != 0:
        last_line = log.strip().splitlines()[-1] if log.strip() else f"exit {process.returncode}"
        raise AudioError(f"ffmpeg failed: {last_line}")
    return log
