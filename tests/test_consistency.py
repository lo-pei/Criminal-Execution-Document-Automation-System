"""刑期一致性校验（载明值 vs 计算值）测试。

这一层只回答「两边一不一样」，不回答「该用哪一边」。所以断言的重点是：
不一致时必须报出来、报出来时必须带足数字、一致时一句都不许多说。
"""

from __future__ import annotations

from datetime import date

from cedas.calc.consistency import check, implied_offset, verify_span
from cedas.domain.enums import Consistency
from cedas.domain.models import SentenceSpan

SPAN = SentenceSpan(start=date(2024, 1, 16), end=date(2024, 4, 15), months=3)


def test_pass_when_both_ends_match():
    assert verify_span(SPAN, date(2024, 1, 16), date(2024, 4, 15)) is Consistency.PASS


def test_mismatch_when_any_end_differs():
    assert verify_span(SPAN, date(2024, 1, 16), date(2024, 4, 10)) is Consistency.MISMATCH
    assert verify_span(SPAN, date(2023, 12, 1), date(2024, 4, 15)) is Consistency.MISMATCH


def test_no_text_end_when_judgment_is_silent():
    """判决书不写止日（实测 53%）不是错误，是常态，不能报成不一致。"""
    assert verify_span(SPAN, None, None) is Consistency.NO_TEXT_END


def test_not_applicable_when_span_is_empty():
    """起算日还没有时比不了首尾，也不该说「一致」。"""
    assert verify_span(SentenceSpan(), date(2024, 1, 16), date(2024, 4, 15)) is (
        Consistency.NOT_APPLICABLE
    )


def test_mismatch_message_carries_both_sides_and_delta():
    span, warnings = check(
        SPAN,
        date(2024, 1, 16),
        date(2024, 4, 10),
        defendant_label="陈某",
    )

    assert span.consistency is Consistency.MISMATCH
    assert len(warnings) == 1
    message = warnings[0].message
    assert warnings[0].code == "V1"
    assert "陈某" in message
    # 四个日期与差值都要在，复核的人据此回原文核对，不用自己再算一遍。
    assert "2024-01-16" in message and "2024-04-15" in message
    assert "2024-04-10" in message
    assert "晚 5 天" in message


def test_pass_produces_no_warning():
    span, warnings = check(SPAN, date(2024, 1, 16), date(2024, 4, 15))

    assert span.consistency is Consistency.PASS
    assert warnings == []


def test_length_gap_is_reported_when_start_is_unknown():
    """起算日待填时，用载明区间与刑期长短比，差几天就是折抵几天。"""
    span, warnings = check(
        SentenceSpan(),
        date(2014, 4, 17),
        date(2017, 1, 25),
        months=34,
        defendant_label="颜某",
    )

    assert span.consistency is Consistency.NOT_APPLICABLE
    assert len(warnings) == 1
    assert warnings[0].code == "V2"
    assert "短 22 天" in warnings[0].message


def test_no_length_gap_warning_when_span_matches_term():
    _, warnings = check(
        SentenceSpan(),
        date(2020, 5, 11),
        date(2022, 11, 10),
        months=30,
    )

    assert warnings == []


def test_implied_offset_counts_inclusive_days():
    """载明区间含首尾两日，所以「三个月整」的区间比 3 月 − 1 日多一天。"""
    assert implied_offset(
        months=3, days=0, text_start=date(2024, 1, 16), text_end=date(2024, 4, 15)
    ) == 0
    assert implied_offset(
        months=3, days=0, text_start=date(2024, 1, 16), text_end=date(2024, 4, 10)
    ) == 5


def test_implied_offset_is_none_without_enough_input():
    assert implied_offset(months=None, days=0, text_start=date(2024, 1, 16), text_end=date(2024, 4, 10)) is None
    assert implied_offset(months=3, days=0, text_start=None, text_end=date(2024, 4, 10)) is None
