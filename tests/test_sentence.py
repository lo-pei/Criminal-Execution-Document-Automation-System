"""刑期日期计算测试。

这些用例是法定规则的直接映射，改动实现前先确认条文依据
（刑法第 41、44、47 条；ADR-006）。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cedas.calc.sentence import (
    add_months,
    compute_end_date,
    compute_release_date,
    compute_span,
    offset_days,
    resolve_start,
)
from cedas.domain.enums import Consistency, PrincipalType


def test_four_years_from_jan_first():
    """四年有期徒刑自 2023-01-01 起算，届满日为 2026-12-31，不是 2027-01-01。"""
    assert compute_end_date(date(2023, 1, 1), 48) == date(2026, 12, 31)


def test_open_ended_values():
    assert compute_release_date(date(2026, 12, 31)) == date(2027, 1, 1)


def test_one_month_from_month_end_clamps_then_subtracts():
    """1 月 31 日起算一个月 → 2 月 28 日 → 届满 2 月 27 日（ADR-006 待确认边界）。"""
    assert compute_end_date(date(2023, 1, 31), 1) == date(2023, 2, 27)


def test_leap_year_clamp():
    assert compute_end_date(date(2024, 2, 29), 12) == date(2025, 2, 27)


def test_single_day_sentence():
    assert compute_end_date(date(2023, 5, 10), 1) == date(2023, 6, 9)


def test_day_component_follows_month():
    """拘役一个月十五天：先加月，再加日，最后减 1 日。

    （2026）皖0881刑初147号原文：「判处拘役一个月十五天……自2026年9月30日至
    2026年11月13日止」。先加日再加月会得 2026-11-14，差一天。
    """
    assert compute_end_date(date(2026, 9, 30), 1, 15) == date(2026, 11, 13)


def test_days_only_sentence():
    """只写日数的短刑期（拘役十五日）也要能算，不能因为月数为零就拒绝。"""
    assert compute_end_date(date(2026, 1, 1), 0, 15) == date(2026, 1, 15)


def test_zero_month_and_zero_day_rejected():
    with pytest.raises(ValueError):
        compute_end_date(date(2026, 1, 1), 0, 0)


def test_add_months_clamps_day():
    assert add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)


def test_add_months_rejects_negative():
    with pytest.raises(ValueError):
        add_months(date(2023, 1, 1), -1)


def test_compute_end_date_rejects_zero():
    with pytest.raises(ValueError):
        compute_end_date(date(2023, 1, 1), 0)


@pytest.mark.parametrize(
    ("principal", "custody", "expected"),
    [
        (PrincipalType.CONTROL, 10, 20),
        (PrincipalType.DETENTION, 10, 10),
        (PrincipalType.FIXED_TERM, 10, 10),
        (PrincipalType.LIFE, 10, 0),
        (PrincipalType.DEATH, 10, 0),
    ],
)
def test_offset_days(principal, custody, expected):
    assert offset_days(principal, custody) == expected


def test_fixed_term_does_not_double_count_custody():
    """有期徒刑的羁押期已含在起算日里，届满日不得再被扣减。"""
    span = compute_span(
        principal_type=PrincipalType.FIXED_TERM,
        months=48,
        start=date(2023, 1, 1),
        custody_days=30,
    )
    assert span.end == date(2026, 12, 31)
    assert span.offset_days == 30


def test_control_subtracts_offset_from_end():
    span = compute_span(
        principal_type=PrincipalType.CONTROL,
        months=24,
        start=date(2023, 5, 1),
        custody_days=3,
    )
    assert span.end == date(2025, 4, 30) - timedelta(days=6)
    assert span.offset_days == 6


def test_span_is_not_applicable_without_start():
    span = compute_span(principal_type=PrincipalType.FIXED_TERM, months=48, start=None)
    assert span.end is None
    assert span.consistency is Consistency.NOT_APPLICABLE


def test_life_sentence_has_no_span():
    span = compute_span(principal_type=PrincipalType.LIFE, months=None, start=date(2023, 1, 1))
    assert span.end is None


def test_resolve_start_prefers_custody_for_fixed_term():
    assert resolve_start(
        PrincipalType.FIXED_TERM,
        custody_start=date(2023, 3, 1),
        effective_date=date(2023, 6, 1),
    ) == date(2023, 3, 1)


def test_resolve_start_prefers_effective_date_for_control():
    assert resolve_start(
        PrincipalType.CONTROL,
        custody_start=date(2023, 3, 1),
        effective_date=date(2023, 6, 1),
    ) == date(2023, 6, 1)


def test_resolve_start_falls_back_for_defendant_not_in_custody():
    assert resolve_start(PrincipalType.FIXED_TERM, effective_date=date(2023, 6, 1)) == date(2023, 6, 1)
