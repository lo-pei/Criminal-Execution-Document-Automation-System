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


def _find_marker(
    paragraphs: Sequence[ProseParagraph], markers: Sequence[str], *, last: bool = False
) -> Optional[int]:
    """找标记所在的段落下标。``last=True`` 取最后一处。"""
    found: Optional[int] = None
    for index, paragraph in enumerate(paragraphs):
        if any(marker in paragraph.text for marker in markers):
            if not last:
                return index
            found = index
    return found


def _strip_marker(text: str, markers: Sequence[str]) -> str:
    for marker in markers:
        text = text.replace(marker, "")
    return text.strip(" 　：:，,。")


def text_before_end_marker(text: str) -> str:
    """切掉段落里属于落款的那部分（「如不服本判决…」及其后文字）。

    清洗层按「上一行有没有句末标点」决定拼不拼接，而刑期注释段以「）」结尾，
    不算句末标点，于是真实判决里最后一名被告人的注释段常常和上诉告知挤在同一
    自然段：

        （刑期从判决执行之日起计算……即自2024年9月19日起至2025年3月18日止。罚金已缴纳）如不服本判决，可在……

    这一段的前半属主文、后半属落款，按段落归属只能二选一。切在标记处，两边都拿得到。
    """
    cut = len(text)
    for marker in BODY_END_MARKERS:
        position = text.find(marker)
        if position != -1:
            cut = min(cut, position)
    return text[:cut]


def locate(paragraphs: Sequence[ProseParagraph]) -> Anchors:
    """定位三段区间。

    **取最后一处「判决如下」。** 再审与二审判决书会把原审判决全文引述进来，
    里面带着原审自己的「本院认为……判决如下：」。取第一处，主文区间就从**原审**
    的判项开始，一直延伸到「如不服本判决」——原审判项、原审说理全被当成本案主文。
    （2015）金义刑再初字第1号就是这样：只有两项判项的再审判决，主文区间里却
    塞进了原审的「判处拘役四个月，缓刑八个月」。

    一审文书通常只有一处标记，取最后一处与取第一处等价，因此这个改动对一审无影响。

    Raises:
        AnchorNotFoundError: 找不到「判决如下」。先查清洗结果，别急着改正则。
    """
    start_index = _find_marker(paragraphs, BODY_START_MARKERS, last=True)
    if start_index is None:
        raise AnchorNotFoundError(
            "未找到主文锚点「判决如下」。请先用 `cedas clean` 检查清洗后的段落划分。"
        )

    # 「判决如下：」可能独占一段，也可能与第一项判决同段
    remainder = _strip_marker(paragraphs[start_index].text, BODY_START_MARKERS)
    body_first = start_index if remainder else start_index + 1

    end_index = _find_marker(paragraphs[body_first:], BODY_END_MARKERS)
    if end_index is None:
        body_stop = len(paragraphs)
        body_last = len(paragraphs)
    else:
        body_last = body_first + end_index
        # 落款标记常与最后一项判决挤在同一段（见 :func:`text_before_end_marker`）。
        # 标记之前还有内容时，这一段仍算主文——不并进去，最后一名被告人的
        # 载明起止日就落在锚点之外，一份两人判决只有一个人抽到，且不会报错。
        has_body_part = bool(text_before_end_marker(paragraphs[body_last].text).strip())
        body_stop = body_last + 1 if has_body_part else body_last

    return Anchors(
        head=slice(0, start_index),
        body=slice(body_first, body_stop),
        # 交界那一段两边都要：主文要它的前半，落款要它的后半。
        tail=slice(body_last, len(paragraphs)),
        body_start_index=body_first,
        body_end_index=body_last,
    )


__all__ = [
    "BODY_END_MARKERS",
    "BODY_START_MARKERS",
    "AnchorNotFoundError",
    "Anchors",
    "locate",
    "text_before_end_marker",
]
