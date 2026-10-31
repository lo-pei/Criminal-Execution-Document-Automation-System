"""D 组（程序性）字段：中文日期解析与落款抽取。

重点锁两件事：

1. 年份是**逐位读**（二〇二三 = 2023），不是位值读（按位值是 3）。
   这个错误不会抛异常，只会让整份文书的日期差二十年。
2. 落款区间**最后一个**日期才是判决日期；正序取第一个会命中正文里的其他日期。
"""

from __future__ import annotations

from datetime import date

from cedas.domain.models import Case, ProseParagraph
from cedas.extract.anchors import locate
from cedas.extract.case_info import (
    cn_small_number,
    cn_year,
    extract_case_info,
    parse_cn_date,
)

TAIL = [
    "北京市朝阳区人民法院",
    "刑事判决书",
    "（2023）京0105刑初9号",
    "公诉机关北京市朝阳区人民检察院。",
    "判决如下：",
    "一、被告人李四犯盗窃罪，判处拘役四个月。",
    "如不服本判决，可在接到判决书的第二日起十日内提出上诉。",
    "审判员王五",
    "二〇二三年五月十日",
    "书记员钱七",
]


def _paragraphs(texts: list[str]) -> list[ProseParagraph]:
    return [ProseParagraph(index=i, text=text) for i, text in enumerate(texts)]


def test_cn_year_is_read_digit_by_digit():
    assert cn_year("二〇二三") == 2023
    assert cn_year("二〇二三") != 3, "按位值读会得 3 —— 差二十年，且不会报错"
    assert cn_year("二零二五") == 2025
    assert cn_year("2024") == 2024
    assert cn_year("二年") is None, "不足四位不猜"


def test_cn_small_number():
    assert cn_small_number("五") == 5
    assert cn_small_number("十") == 10
    assert cn_small_number("十二") == 12
    assert cn_small_number("二十") == 20
    assert cn_small_number("二十五") == 25
    assert cn_small_number("三十一") == 31
    assert cn_small_number("31") == 31
    assert cn_small_number("廿") is None


def test_parse_cn_date_mixed_styles():
    assert parse_cn_date("二〇二三年五月一日") == date(2023, 5, 1)
    assert parse_cn_date("2023年5月20日") == date(2023, 5, 20)
    assert parse_cn_date("落款：二〇二五年十二月三十一日。") == date(2025, 12, 31)


def test_parse_cn_date_rejects_impossible_date():
    assert parse_cn_date("二〇二三年二月三十日") is None, "2 月 30 日不存在，宁可留空"
    assert parse_cn_date("没有日期") is None
    assert parse_cn_date("") is None


def test_extract_case_info_fills_tail_fields():
    case = Case()
    paragraphs = _paragraphs(TAIL)
    warnings = extract_case_info(case, paragraphs, locate(paragraphs))

    assert case.judgment_date.value == date(2023, 5, 10)
    assert case.panel.value == "审判员王五"
    assert case.clerk.value == "钱七"
    assert case.prosecutor_organ.value == "北京市朝阳区人民检察院"
    assert warnings == []
    assert case.judgment_date.provenance is not None
    assert case.judgment_date.provenance.paragraph_index == 8


def test_extract_case_info_takes_last_tail_date():
    """落款区出现两个日期时取最后一个——前一个常是送达/宣判日期残留。"""
    texts = [*TAIL[:7], "二〇二三年五月八日", "二〇二三年五月十日", "书记员钱七"]
    case = Case()
    extract_case_info(case, _paragraphs(texts), locate(_paragraphs(texts)))
    assert case.judgment_date.value == date(2023, 5, 10)


def test_extract_case_info_warns_when_date_missing():
    texts = [text for text in TAIL if "二〇二三年" not in text]
    case = Case()
    paragraphs = _paragraphs(texts)
    warnings = extract_case_info(case, paragraphs, locate(paragraphs))

    assert case.judgment_date.value is None
    assert [warning.code for warning in warnings] == ["A005"]


def test_extract_case_info_does_not_overwrite_existing():
    case = Case()
    case.judgment_date.set(date(2023, 6, 1))
    paragraphs = _paragraphs(TAIL)
    extract_case_info(case, paragraphs, locate(paragraphs))
    assert case.judgment_date.value == date(2023, 6, 1)
