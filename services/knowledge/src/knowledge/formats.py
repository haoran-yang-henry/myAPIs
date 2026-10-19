"""Turn each supported content format into blocks, keeping its structure.

Adding a source format means adding a function here (or a separate extractor
service that sends `blocks`); chunking doesn't change.
"""

import re

from .models import Block, BlockKind, BlocksContent, Content, MarkdownContent, TranscriptContent

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|")


def to_blocks(content: Content) -> list[Block]:
    match content:
        case MarkdownContent():
            return markdown_blocks(content.text)
        case TranscriptContent():
            return [
                Block(
                    text=segment.text,
                    kind="utterance",
                    start_s=segment.start_s,
                    end_s=segment.end_s,
                    speaker=segment.speaker,
                )
                for segment in content.segments
                if segment.text.strip()
            ]
        case BlocksContent():
            return [block for block in content.blocks if block.text.strip()]


def markdown_blocks(text: str) -> list[Block]:
    """Headings, fenced code, and blank-line separated paragraphs, lists and tables."""
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        body = "\n".join(paragraph).strip()
        if body:
            blocks.append(Block(text=body, kind=_kind_of(paragraph[0])))
        paragraph.clear()

    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if fence := _FENCE.match(line):
            flush()
            code = [line]
            i += 1
            while i < len(lines):
                code.append(lines[i])
                if lines[i].strip().startswith(fence.group(1)):
                    break
                i += 1
            blocks.append(Block(text="\n".join(code), kind="code"))
        elif (heading := _HEADING.match(line)) and heading.group(2):
            flush()
            blocks.append(Block(text=heading.group(2), kind="heading", level=len(heading.group(1))))
        elif line.strip():
            paragraph.append(line)
        else:
            flush()
        i += 1
    flush()
    return blocks


def _kind_of(first_line: str) -> BlockKind:
    if _TABLE_ROW.match(first_line):
        return "table"
    if _LIST_ITEM.match(first_line):
        return "list"
    return "paragraph"
