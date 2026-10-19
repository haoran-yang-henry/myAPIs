from knowledge.formats import markdown_blocks, to_blocks
from knowledge.models import TranscriptContent

MARKDOWN = """# 项目计划

目标：三个月上线。
第二行属于同一段。

## 任务

- 设计
- 开发

| 名字 | 角色 |
|---|---|
| 王 | 开发 |

```python
def f():

    return 1
```

#没有空格的不是标题
"""


def test_markdown_keeps_structure() -> None:
    blocks = markdown_blocks(MARKDOWN)
    assert [(b.kind, b.level) for b in blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("list", None),
        ("table", None),
        ("code", None),
        ("paragraph", None),
    ]
    assert blocks[0].text == "项目计划"
    assert blocks[1].text == "目标：三个月上线。\n第二行属于同一段。"
    # A blank line inside a code fence doesn't end the block.
    assert blocks[5].text == "```python\ndef f():\n\n    return 1\n```"


def test_transcript_segments_become_utterances() -> None:
    content = TranscriptContent.model_validate(
        {
            "format": "transcript",
            "recording_id": "ignored",
            "segments": [
                {"start_s": 0, "end_s": 180, "text": "大家好"},
                {"start_s": 180, "end_s": 200, "text": "  "},
            ],
        }
    )
    (block,) = to_blocks(content)
    assert (block.kind, block.text, block.start_s, block.end_s) == ("utterance", "大家好", 0, 180)
