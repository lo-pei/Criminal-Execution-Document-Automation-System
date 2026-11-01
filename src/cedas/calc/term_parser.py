"""中文刑期解析。

输入形如「有期徒刑三年六个月」，输出主刑种类与总月数。
本模块只做解析，不做日期运算——日期在 ``sentence.py``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..domain.enums import PrincipalType


class TermParseError(ValueError):
    """刑期表述无法解析。调用方应记 ``FieldStatus.UNPARSED`` 而非猜测。"""


_CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "○": 0,
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

# 主刑关键词。顺序有意义：先匹配长者，避免「有期徒刑」被「拘役」等误切。
_TYPE_PATTERN = re.compile(r"(有期徒刑|无期徒刑|拘役|管制|死刑)")

_NUM = r"([0-9]+|[零〇○一二三四五六七八九十百两]+)"
_YEAR_PATTERN = re.compile(_NUM + r"\s*年")
_MONTH_PATTERN = re.compile(_NUM + r"\s*个?月")
# 「日」「天」两种写法在实务中都常见：「拘役一个月十五日」「拘役一个月十五天」。
# 这个分量必须单独取，不能并入月数——真实判决里它直接决定届满日相差半个月。
_DAY_PATTERN = re.compile(_NUM + r"\s*[日天]")

# 遇到这些字符即认为刑期表述结束（后面是附加刑、缓刑等内容）
_BODY_TERMINATORS = ("，", ",", "。", "；", ";", "缓刑")


@dataclass(frozen=True)
class ParsedTerm:
    """刑期解析结果。

    ``months`` 与 ``days`` 是**并列的两个分量**，不是折算关系：
    「拘役一个月十五日」= 1 个月 + 15 日，届满日按「先加月、再加日、再减 1 日」
    计算。实测（2026）皖0881刑初147号等真实文书，先加日再加月会差一天。

    ``months`` 为 ``None`` 表示无确定期限（无期徒刑、死刑）。
    """

    principal_type: PrincipalType
    months: Optional[int]
    raw: str
    suspended: bool = False
    days: int = 0


def cn_number_to_int(text: str) -> int:
    """中文数字转整数。支持「十五」「二十五」「三年」等形态，也接受阿拉伯数字。"""
    text = text.strip()
    if not text:
        raise TermParseError("空数字串")
    if text.isdigit():
        return int(text)

    total = 0
    section = 0
    for ch in text:
        if ch in _CN_DIGIT:
            section = _CN_DIGIT[ch]
        elif ch == "十":
            total += (section or 1) * 10
            section = 0
        elif ch == "百":
            total += (section or 1) * 100
            section = 0
        else:
            raise TermParseError(f"无法识别的数字字符：{ch!r}（原文 {text!r}）")
    return total + section


def parse_duration_months(text: str) -> int:
    """解析不带主刑关键词的期限，返回总月数。

    判决书里的缓刑考验期、羁押折抵只写「三年零六个月」「六个月」，前面没有
    「有期徒刑」三个字，:func:`parse_term` 用不了。

    这个函数存在的直接原因是**一次静默少算**：（2023）鲁0811刑初1080号判
    「有期徒刑三年，缓刑三年零六个月」，旧实现按 ``缓刑([\\u4e00-\\u9fa5\\d]{1,6}年)``
    取到「三年」就把后面的「零六个月」丢了，考验期变成 36 个月——不报错、不留空，
    是一个看起来完全正常的错值。

    >>> parse_duration_months("三年零六个月")
    42
    >>> parse_duration_months("六个月")
    6

    Raises:
        TermParseError: 找不到任何年月数字。
    """
    months = 0
    year_match = _YEAR_PATTERN.search(text)
    if year_match:
        months += cn_number_to_int(year_match.group(1)) * 12
    month_match = _MONTH_PATTERN.search(text)
    if month_match:
        months += cn_number_to_int(month_match.group(1))
    if months == 0:
        raise TermParseError(f"找不到期限数字：{text!r}")
    return months


def _cut_body(body: str) -> str:
    """截断到第一个终止符，剥掉附加刑与缓刑表述。"""
    cut = len(body)
    for terminator in _BODY_TERMINATORS:
        pos = body.find(terminator)
        if pos != -1:
            cut = min(cut, pos)
    return body[:cut]


def parse_term(text: str) -> ParsedTerm:
    """解析刑期表述。

    >>> parse_term("有期徒刑三年六个月").months
    42
    >>> parse_term("拘役一个月十五日").days
    15
    >>> parse_term("无期徒刑").months is None
    True

    Raises:
        TermParseError: 找不到主刑关键词，或有期限的主刑缺少年月日数字。
    """
    if not text or not text.strip():
        raise TermParseError("空字符串")

    raw = text.strip()
    match = _TYPE_PATTERN.search(raw)
    if match is None:
        raise TermParseError(f"未找到主刑关键词：{raw!r}")

    keyword = match.group(1)
    principal = PrincipalType(keyword)

    if principal in (PrincipalType.LIFE, PrincipalType.DEATH):
        # 「死刑缓期二年执行」仍属死刑，但执行路径不同，标记出来供上层判断
        return ParsedTerm(principal, None, raw, suspended="缓期" in raw)

    body = _cut_body(raw[match.end() :])

    years = 0
    months = 0
    days = 0
    year_match = _YEAR_PATTERN.search(body)
    if year_match:
        years = cn_number_to_int(year_match.group(1))
    month_match = _MONTH_PATTERN.search(body)
    if month_match:
        months = cn_number_to_int(month_match.group(1))
    day_match = _DAY_PATTERN.search(body)
    if day_match:
        days = cn_number_to_int(day_match.group(1))

    if years == 0 and months == 0 and days == 0:
        raise TermParseError(f"有期限主刑缺少年月日数字：{raw!r}")

    return ParsedTerm(principal, years * 12 + months, raw, days=days)


__all__ = [
    "ParsedTerm",
    "TermParseError",
    "cn_number_to_int",
    "parse_duration_months",
    "parse_term",
]
