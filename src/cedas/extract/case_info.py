"""D 组（程序性）字段抽取：判决日期、审判组织、书记员、公诉机关。

这三项只出现在**落款区间**（``anchors.tail``）与首部的固定位置，位置本身
就是最强的判据，不需要复杂句式规则：

- 落款最后一行日期即判决日期（上诉期那一句里没有日期，不会被误取）
- 「审判长／审判员／代理审判员／人民陪审员 + 姓名」独占一段
- 「书记员 + 姓名」独占一段

中文数字日期（「二〇二三年五月一日」）是法院落款的标准写法，**不能复用
``calc.term_parser.cn_number_to_int``**：那个函数按位值解读，「二〇二三」
会被算成 3（二千零百二十三）。年份是逐位读、月日按位值读，两种读法必须分开。

依据：字段字典 v0.1 §5（D 组），《法院刑事诉讼文书样式》刑事判决书落款结构。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Optional

from ..domain.models import Case, ExtractionWarning, FieldValue, ProseParagraph
from .anchors import Anchors

# 年份逐位读：把字符直接映射成数字，不参与位值运算
_CN_DIGIT = {
    "〇": 0,
    "零": 0,
    "○": 0,
    "Ｏ": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_YEAR = r"(?:[〇零○一二三四五六七八九]{4}|\d{4})"
_SMALL = r"(?:[一二三四五六七八九十]{1,3}|\d{1,2})"
_DATE_RE = re.compile(rf"({_YEAR})\s*年\s*({_SMALL})\s*月\s*({_SMALL})\s*日")

# 姓名限定 2–4 个汉字：判决书落款不会出现「审判长」后接职务或空行
_PANEL_RE = re.compile(r"^(审判长|审判员|代理审判员|人民陪审员)\s*([\u4e00-\u9fa5]{2,4})$")
_CLERK_RE = re.compile(r"^书记员\s*([\u4e00-\u9fa5]{2,4})$")
_PROSECUTOR_RE = re.compile(r"^公诉机关\s*([^。，；\s]+)")


def cn_small_number(text: str) -> Optional[int]:
    """「五」「十」「十二」「二十五」转整数，只用于月、日。

    认不出返回 ``None``，不抛异常——落款日期读错会污染整份文书，宁可留空。
    """
    raw = text.strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)

    if "十" not in raw:
        return _CN_DIGIT.get(raw) if len(raw) == 1 else None

    left, _, right = raw.partition("十")
    if len(left) > 1 or len(right) > 1:
        return None
    tens = 1 if not left else _CN_DIGIT.get(left)
    ones = 0 if not right else _CN_DIGIT.get(right)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def cn_year(text: str) -> Optional[int]:
    """中文年份逐位读：「二〇二三」→ 2023。

    这不是位值读法。「二〇二三」按位值是 2003（二千零二十三），差 20 年。
    """
    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    if len(raw) != 4:
        return None
    digits = [_CN_DIGIT.get(ch) for ch in raw]
    if any(digit is None for digit in digits):
        return None
    return int("".join(str(digit) for digit in digits))


def parse_cn_date(text: str) -> Optional[date]:
    """从任意文本中取**第一个**中文/阿拉伯混写日期。

    >>> parse_cn_date("二〇二三年五月一日").isoformat()
    '2023-05-01'
    >>> parse_cn_date("2023年5月20日").isoformat()
    '2023-05-20'
    >>> parse_cn_date("没有日期") is None
    True
    """
    match = _DATE_RE.search(text or "")
    if match is None:
        return None

    year = cn_year(match.group(1))
    month = cn_small_number(match.group(2))
    day = cn_small_number(match.group(3))
    if year is None or month is None or day is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _field(value: object, paragraph: ProseParagraph, extractor: str) -> FieldValue:
    from ..domain.models import Provenance

    field: FieldValue = FieldValue()
    return field.set(
        value,
        provenance=Provenance(
            paragraph_index=paragraph.index,
            snippet=paragraph.text[:120],
            page=paragraph.page,
        ),
        extractor=extractor,
        confidence=1.0,
    )


def _tail(paragraphs: Sequence[ProseParagraph], anchors: Anchors) -> list[ProseParagraph]:
    return list(paragraphs[anchors.tail])


def _find_date(paragraphs: Sequence[ProseParagraph], anchors: Anchors):
    """落款区间**最后一个**日期即判决日期。

    倒序遍历：「如不服本判决，可在接到判决书的第二日起十日内…」不含日期，
    真正的落款日期在最后。正序取第一个反而可能命中正文尾部残留的日期。
    """
    for paragraph in reversed(_tail(paragraphs, anchors)):
        found = parse_cn_date(paragraph.text)
        if found is not None:
            return found, paragraph
    return None, None


def extract_case_info(
    case: Case,
    paragraphs: Sequence[ProseParagraph],
    anchors: Anchors,
) -> list[ExtractionWarning]:
    """回填案件级程序性字段。只填空缺项，不覆盖已有值。"""
    warnings: list[ExtractionWarning] = []

    if not case.judgment_date.is_present:
        found, paragraph = _find_date(paragraphs, anchors)
        if found is not None and paragraph is not None:
            case.judgment_date = _field(found, paragraph, "tail_date@v1")

    tail = _tail(paragraphs, anchors)
    for paragraph in tail:
        text = paragraph.text.strip()
        if not case.panel.is_present:
            match = _PANEL_RE.match(text)
            if match:
                case.panel = _field(
                    f"{match.group(1)}{match.group(2)}", paragraph, "tail_panel@v1"
                )
                continue
        if not case.clerk.is_present:
            match = _CLERK_RE.match(text)
            if match:
                case.clerk = _field(match.group(1), paragraph, "tail_clerk@v1")

    if not case.prosecutor_organ.is_present:
        for paragraph in paragraphs[anchors.head]:
            match = _PROSECUTOR_RE.match(paragraph.text.strip())
            if match:
                case.prosecutor_organ = _field(match.group(1), paragraph, "head_prosecutor@v1")
                break

    if not case.judgment_date.is_present:
        warnings.append(
            ExtractionWarning(
                code="A005",
                message="未识别判决日期：落款区未找到「二〇二三年五月一日」式日期，需人工录入",
                field="judgment_date",
            )
        )
    return warnings


__all__ = ["cn_small_number", "cn_year", "extract_case_info", "parse_cn_date"]
