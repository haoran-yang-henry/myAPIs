"""Chunking shared by every source format.

Blocks arrive with their structure; consecutive blocks are packed into chunks of
about `target` tokens:
- a heading always starts a new chunk, so a chunk never mixes sections;
- a block longer than `max_tokens` is split at sentence ends (code and tables at line
  ends), and its time range is spread over the pieces by length;
- consecutive chunks of a section share up to `overlap` tokens of whole sentences,
  so a sentence on a boundary is still found together with its context.
"""

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

from .models import Block

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
# Ends a sentence: CJK punctuation, or Western punctuation followed by whitespace,
# plus any closing quotes or brackets; or a line break.
_SENTENCE_END = re.compile(r"(?:[。！？；…]+|[.!?;]+(?=\s|$))[”’」』）)\"']*\s*|\n+")


def estimate_tokens(text: str) -> int:
    """Rough size in tokens of OpenAI's embedding tokenizer, used only to size chunks:
    it spends 1-2 tokens per CJK character and about one per 4 other characters."""
    cjk = len(_CJK.findall(text))
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 4)


def split_sentences(text: str) -> list[str]:
    """Split after sentence-ending punctuation. The pieces concatenate back to text."""
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        pieces.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


@dataclass(frozen=True)
class Chunk:
    text: str
    tokens: int
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    start_s: float | None
    end_s: float | None
    speakers: list[str]


@dataclass(frozen=True)
class _Unit:
    """A whole block, or one piece of a long block."""

    text: str
    tokens: int
    block: int  # index of the source block; pieces of one block join without a separator
    heading_path: tuple[str, ...]
    page: int | None
    start_s: float | None
    end_s: float | None
    speaker: str | None


_SECTION_BREAK = None


def chunk_blocks(blocks: list[Block], *, target: int, max_tokens: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[_Unit] = []
    for unit in _units(blocks, max_tokens):
        if unit is _SECTION_BREAK:
            if current:
                chunks.append(_join(current))
            current = []
            continue
        if current and _size(current) + unit.tokens > target:
            chunks.append(_join(current))
            tail = _tail(current, overlap)
            current = tail if _size(tail) + unit.tokens <= max_tokens else []
        current.append(unit)
    if current:
        chunks.append(_join(current))
    return chunks


def _units(blocks: list[Block], max_tokens: int) -> Iterator[_Unit | None]:
    path: list[tuple[int, str]] = []  # (heading level, heading text)
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            level = block.level or 1
            path = [(lvl, title) for lvl, title in path if lvl < level]
            path.append((level, block.text.strip()))
            yield _SECTION_BREAK
            continue
        pieces = (
            [block.text] if estimate_tokens(block.text) <= max_tokens else _split(block, max_tokens)
        )
        for piece, (start, end) in zip(pieces, _spread(pieces, block), strict=True):
            yield _Unit(
                text=piece,
                tokens=estimate_tokens(piece),
                block=index,
                heading_path=tuple(title for _, title in path),
                page=block.page,
                start_s=start,
                end_s=end,
                speaker=block.speaker,
            )


def _split(block: Block, max_tokens: int) -> list[str]:
    if block.kind in ("code", "table"):
        parts = block.text.splitlines(keepends=True)
    else:
        parts = split_sentences(block.text)
    pieces: list[str] = []
    for part in parts:
        tokens = estimate_tokens(part)
        if tokens <= max_tokens:
            pieces.append(part)
        else:  # a "sentence" with no punctuation at all: cut it by length
            width = max(1, len(part) * max_tokens // tokens)
            pieces.extend(part[i : i + width] for i in range(0, len(part), width))
    return pieces


def _spread(pieces: list[str], block: Block) -> list[tuple[float | None, float | None]]:
    """Each piece's share of the block's time range, proportional to its length."""
    if block.start_s is None or block.end_s is None:
        return [(block.start_s, block.end_s)] * len(pieces)
    total = sum(len(piece) for piece in pieces) or 1
    ranges, at = [], block.start_s
    for piece in pieces:
        span = (block.end_s - block.start_s) * len(piece) / total
        ranges.append((round(at, 2), round(at + span, 2)))
        at += span
    return ranges


def _size(units: list[_Unit]) -> int:
    return sum(unit.tokens for unit in units)


def _tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """The last whole units that fit in `overlap` tokens."""
    tail: list[_Unit] = []
    for unit in reversed(units):
        if _size(tail) + unit.tokens > overlap:
            break
        tail.insert(0, unit)
    return tail


def _join(units: list[_Unit]) -> Chunk:
    text = units[0].text
    for previous, unit in pairwise(units):
        text += ("" if unit.block == previous.block else "\n\n") + unit.text
    text = text.strip()
    pages = [unit.page for unit in units if unit.page is not None]
    starts = [unit.start_s for unit in units if unit.start_s is not None]
    ends = [unit.end_s for unit in units if unit.end_s is not None]
    return Chunk(
        text=text,
        tokens=estimate_tokens(text),
        heading_path=list(units[0].heading_path),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        start_s=min(starts) if starts else None,
        end_s=max(ends) if ends else None,
        speakers=list(dict.fromkeys(unit.speaker for unit in units if unit.speaker)),
    )
