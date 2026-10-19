import pytest

from knowledge.chunking import chunk_blocks, estimate_tokens, split_sentences
from knowledge.models import Block

SIZES = {"target": 500, "max_tokens": 800, "overlap": 60}


def test_estimate_tokens() -> None:
    assert estimate_tokens("你好世界") == 6
    assert estimate_tokens("hello world!") == 3
    assert estimate_tokens("") == 0


def test_split_sentences() -> None:
    text = (
        "大家好。今天讨论预算！谁负责？版本是 3.5，没问题. Next: ship it! "
        "他说：“好。”然后走了\n下一行"
    )
    pieces = split_sentences(text)
    assert "".join(pieces) == text
    assert pieces == [
        "大家好。",
        "今天讨论预算！",
        "谁负责？",
        "版本是 3.5，没问题. ",
        "Next: ship it! ",
        "他说：“好。”",
        "然后走了\n",
        "下一行",
    ]


def test_each_heading_starts_a_chunk() -> None:
    blocks = [
        Block(text="项目计划", kind="heading", level=1),
        Block(text="目标是三个月上线。"),
        Block(text="预算", kind="heading", level=2),
        Block(text="总预算十万。"),
        Block(text="人员", kind="heading", level=2),
        Block(text="两个人。"),
    ]
    chunks = chunk_blocks(blocks, **SIZES)
    assert [(c.heading_path, c.text) for c in chunks] == [
        (["项目计划"], "目标是三个月上线。"),
        (["项目计划", "预算"], "总预算十万。"),
        (["项目计划", "人员"], "两个人。"),
    ]


def test_small_blocks_are_packed_up_to_target() -> None:
    blocks = [Block(text=f"{i}" * 400) for i in range(5)]  # 100 tokens each
    chunks = chunk_blocks(blocks, target=250, max_tokens=400, overlap=0)
    assert [c.text for c in chunks] == [
        "0" * 400 + "\n\n" + "1" * 400,
        "2" * 400 + "\n\n" + "3" * 400,
        "4" * 400,
    ]


def test_long_utterance_is_split_with_times_and_overlap() -> None:
    # 100 sentences of 10 tokens and 10 seconds each.
    sentences = [f"第{i:02d}项讨论预算。" for i in range(100)]
    block = Block(text="".join(sentences), kind="utterance", start_s=0, end_s=1000)

    chunks = chunk_blocks([block], **SIZES)

    assert [(c.start_s, c.end_s) for c in chunks] == [
        pytest.approx((0, 500)),
        pytest.approx((440, 940)),
        pytest.approx((880, 1000)),
    ]
    # The last 6 sentences (60 tokens) of a chunk open the next one.
    assert chunks[1].text.startswith("".join(sentences[44:50]))
    assert chunks[0].text.endswith("".join(sentences[44:50]))


def test_long_code_splits_at_line_ends() -> None:
    code = "\n".join(f"line_{i} = compute({i})" for i in range(300))
    chunks = chunk_blocks([Block(text=code, kind="code")], target=500, max_tokens=800, overlap=0)
    assert len(chunks) > 1
    assert "\n".join(c.text for c in chunks) == code


def test_text_without_punctuation_is_cut_by_length() -> None:
    chunks = chunk_blocks([Block(text="字" * 3000)], target=500, max_tokens=800, overlap=0)
    assert all(c.tokens <= 800 for c in chunks)
    assert "".join(c.text for c in chunks) == "字" * 3000


def test_pages_and_speakers_are_collected() -> None:
    blocks = [
        Block(text="甲说的话。", kind="utterance", speaker="A", page=1),
        Block(text="乙说的话。", kind="utterance", speaker="B", page=2),
    ]
    (chunk,) = chunk_blocks(blocks, **SIZES)
    assert (chunk.page_start, chunk.page_end, chunk.speakers) == (1, 2, ["A", "B"])
