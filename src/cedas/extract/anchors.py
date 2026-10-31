"""三段锚点定位。

判决书的可抽取信息集中在三个法定结构位置，先定位结构再套规则，
比全文盲目正则稳定得多（见 01-需求与可行性评估 §3.4、§3.5）。

- 首部：文书标题至「判决如下」之前
- 主文：「判决如下」之后至「如不服本判决」之前
- 落款：「如不服本判决」之后至文末
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from ..domain.models import ProseParagraph

BODY_START_MARKERS = ("判决如下",)

BODY_END_MARKERS = (
    "如不服本判决",
    "本判决为终审判决",
    "本裁定为终审裁定",
    "本判决自送达之日起发生法律效力",
)


class AnchorNotFoundError(LookupError):
    """主文锚点缺失。这通常意味着清洗层出了问题，而不是规则写错了。"""


@dataclass
class Anchors:
    """三个区间的段落下标切片。"""

    head: slice
    body: slice
    tail: slice
    body_start_index: int
    body_end_index: int


def _find_marker(paragraphs: Sequence[ProseParagraph], markers: Sequence[str]) -> Optional[int]:
    for index, paragraph in enumerate(paragraphs):
        if any(marker in paragraph.text for marker in markers):
            return index
    return None


def _strip_marker(text: str, markers: Sequence[str]) -> str:
    for marker in markers:
        text = text.replace(marker, "")
    return text.strip(" 　：:，,。")


def locate(paragraphs: Sequence[ProseParagraph]) -> Anchors:
    """定位三段区间。

    Raises:
        AnchorNotFoundError: 找不到「判决如下」。先查清洗结果，别急着改正则。
    """
    start_index = _find_marker(paragraphs, BODY_START_MARKERS)
    if start_index is None:
        raise AnchorNotFoundError(
            "未找到主文锚点「判决如下」。请先用 `cedas clean` 检查清洗后的段落划分。"
        )

    # 「判决如下：」可能独占一段，也可能与第一项判决同段
    remainder = _strip_marker(paragraphs[start_index].text, BODY_START_MARKERS)
    body_first = start_index if remainder else start_index + 1

    end_index = _find_marker(paragraphs[body_first:], BODY_END_MARKERS)
    body_last = body_first + end_index if end_index is not None else len(paragraphs)

    return Anchors(
        head=slice(0, start_index),
        body=slice(body_first, body_last),
        tail=slice(body_last, len(paragraphs)),
        body_start_index=body_first,
        body_end_index=body_last,
    )


__all__ = ["BODY_END_MARKERS", "BODY_START_MARKERS", "AnchorNotFoundError", "Anchors", "locate"]
