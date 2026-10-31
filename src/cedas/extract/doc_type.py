"""文书类型识别：这份文件到底是什么。

只有一件事——给文件名里的「文书类型」那一段取值。识别不出来就留空，
**不要猜**：把起诉书标成判决书，读起来比留空更糟，因为留空会被人看见。

判据按强弱分三层，命中即返回：

1. **标题行**（最强）。判决书首部第 2–3 行就是「刑事判决书」，且短于 30 字。
   标题可能吸附了法院名（「北京市朝阳区人民法院刑事判决书」），所以用后缀匹配。
2. **主文转向句**。「判决如下：」「裁定如下：」是文书类型的自证句，
   比标题更可靠地说明正文性质，但只在标题行没识别出来时兜底——
   标题缺行是版式问题，主文缺句是内容问题，前者更常见。
3. **首部起诉语**。「以……起诉书指控……向本院提起公诉」说明这是判决书之前
   的起诉书，或被误当作判决书导入的起诉材料。

一致性与边界：本模块只读段落文本，不依赖版式坐标，因此 PDF 与 TXT 两个通道
结果相同。识别结果不参与阻断判定——文书类型错了照样能出文，
但名字会错，而名字是以后找回卷宗的唯一线索。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from ..domain.models import ProseParagraph

# 规范名。裁掉「附带民事」会让一份同时判赔偿的判决书看起来像纯刑事案，
# 所以它是独立类型而不是「刑事判决书」的一个别名。
CRIMINAL_JUDGMENT = "刑事判决书"
CRIMINAL_CIVIL_JUDGMENT = "刑事附带民事判决书"
CRIMINAL_RULING = "刑事裁定书"
CRIMINAL_CIVIL_RULING = "刑事附带民事裁定书"
CRIMINAL_INDICTMENT = "刑事起诉书"

# 后缀 → 规范名。**顺序敏感**：长键必须先命中，否则「附带民事判决书」
# 会被「判决书」抢先匹配成一个纯刑事判决书。
_TITLE_MAP: dict[str, str] = {
    "刑事附带民事判决书": CRIMINAL_CIVIL_JUDGMENT,
    "附带民事判决书": CRIMINAL_CIVIL_JUDGMENT,
    "刑事附带民事裁定书": CRIMINAL_CIVIL_RULING,
    "附带民事裁定书": CRIMINAL_CIVIL_RULING,
    "刑事判决书": CRIMINAL_JUDGMENT,
    "判决书": CRIMINAL_JUDGMENT,
    "刑事裁定书": CRIMINAL_RULING,
    "裁定书": CRIMINAL_RULING,
    "刑事起诉书": CRIMINAL_INDICTMENT,
    "起诉书": CRIMINAL_INDICTMENT,
}
_ORDERED_KEYS = tuple(sorted(_TITLE_MAP, key=len, reverse=True))

# 标题只在前 12 段内找：判决书的标题不会排到正文中间。
_TITLE_SCAN = 12
_TITLE_MAX_LEN = 30

_SPACE_RE = re.compile(r"\s+")

# 主文转向句 → 类型。用正则是为了容忍「判决如下：」与「判决如下:」两种冒号。
_TAIL_CLUES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"判决如下"), CRIMINAL_JUDGMENT),
    (re.compile(r"裁定如下"), CRIMINAL_RULING),
)

_INDICTMENT_RE = re.compile(r"以[^，。]{0,40}?起诉书指控")


@dataclass(frozen=True)
class DocKind:
    """识别结果。``name`` 为空串表示没认出来——调用方据此跳过文件名里那一段。"""

    name: str = ""
    confidence: float = 0.0
    index: Optional[int] = None
    evidence: str = ""

    @property
    def known(self) -> bool:
        return bool(self.name)


def _squash(text: str) -> str:
    """去掉全部空白。标题在 PDF 通道里常被拆成「刑事 判决书」两截。"""
    return _SPACE_RE.sub("", text or "")


def detect_doc_kind(paragraphs: Sequence[ProseParagraph]) -> DocKind:
    """识别文书类型。认不出返回空 ``DocKind``，不抛异常。

    >>> from cedas.domain.models import ProseParagraph
    >>> detect_doc_kind([ProseParagraph(0, "刑事判决书")]).name
    '刑事判决书'
    """
    for paragraph in paragraphs[:_TITLE_SCAN]:
        text = _squash(paragraph.text)
        if not text or len(text) > _TITLE_MAX_LEN:
            continue
        for key in _ORDERED_KEYS:
            if text.endswith(key):
                return DocKind(_TITLE_MAP[key], 0.95, paragraph.index, text)

    for paragraph in paragraphs:
        text = paragraph.text or ""
        for pattern, name in _TAIL_CLUES:
            if pattern.search(text):
                return DocKind(name, 0.70, paragraph.index, text.strip()[:40])

    for paragraph in paragraphs[:_TITLE_SCAN]:
        text = paragraph.text or ""
        if _INDICTMENT_RE.search(text):
            return DocKind(CRIMINAL_INDICTMENT, 0.50, paragraph.index, text.strip()[:40])

    return DocKind()


__all__ = [
    "CRIMINAL_CIVIL_JUDGMENT",
    "CRIMINAL_CIVIL_RULING",
    "CRIMINAL_INDICTMENT",
    "CRIMINAL_JUDGMENT",
    "CRIMINAL_RULING",
    "DocKind",
    "detect_doc_kind",
]
