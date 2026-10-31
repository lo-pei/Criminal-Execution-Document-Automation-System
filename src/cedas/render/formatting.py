"""文书排版用的格式化函数。

文书正文对日期与刑期的书写有固定习惯，不能直接把 ISO 字符串塞进模板：
- 日期写作「2023年1月1日」，不补零
- 刑期写作「三年六个月」，不写「42个月」
"""

from __future__ import annotations

from datetime import date
from typing import Optional

_CN_DIGITS = "零一二三四五六七八九"


def format_cn_date(value: Optional[date]) -> str:
    """日期转中文书写。``None`` 返回空串，便于模板填空不报错。"""
    if value is None:
        return ""
    return f"{value.year}年{value.month}月{value.day}日"


def _section_to_chinese(number: int) -> str:
    """0 < number < 100 的中文写法。"""
    if number < 0 or number >= 100:
        raise ValueError("仅支持 1–99")

    tens, ones = divmod(number, 10)
    if tens == 0:
        return _CN_DIGITS[ones]
    prefix = "十" if tens == 1 else _CN_DIGITS[tens] + "十"
    return prefix if ones == 0 else prefix + _CN_DIGITS[ones]


def months_to_chinese(months: Optional[int]) -> str:
    """月数转刑期书写。

    >>> months_to_chinese(42)
    '三年六个月'
    >>> months_to_chinese(120)
    '十年'
    >>> months_to_chinese(10)
    '十个月'
    """
    if months is None or months <= 0:
        return ""

    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{_section_to_chinese(years)}年")
    if rest:
        parts.append(f"{_section_to_chinese(rest)}个月")
    return "".join(parts)


__all__ = ["format_cn_date", "months_to_chinese"]
