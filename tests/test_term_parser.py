"""刑期解析测试。"""

from __future__ import annotations

import pytest

from cedas.calc.term_parser import TermParseError, cn_number_to_int, parse_term
from cedas.domain.enums import PrincipalType


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("有期徒刑三年六个月", 42),
        ("有期徒刑十年", 120),
        ("有期徒刑一年六个月", 18),
        ("有期徒刑三年零六个月", 42),
        ("有期徒刑十五年", 180),
        ("有期徒刑二十五年", 300),
        ("拘役六个月", 6),
        ("管制二年", 24),
    ],
)
def test_parse_determinate_terms(text, expected):
    assert parse_term(text).months == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("有期徒刑三年六个月", PrincipalType.FIXED_TERM),
        ("拘役六个月", PrincipalType.DETENTION),
        ("管制二年", PrincipalType.CONTROL),
        ("无期徒刑", PrincipalType.LIFE),
        ("死刑", PrincipalType.DEATH),
    ],
)
def test_parse_principal_type(text, expected):
    assert parse_term(text).principal_type is expected


def test_life_and_death_have_no_months():
    assert parse_term("无期徒刑").months is None
    assert parse_term("死刑").months is None


def test_death_with_reprieve_is_flagged():
    parsed = parse_term("死刑，缓期二年执行")
    assert parsed.principal_type is PrincipalType.DEATH
    assert parsed.suspended is True


def test_trailing_additional_punishment_is_ignored():
    assert parse_term("有期徒刑三年，并处罚金人民币五千元").months == 36
    assert parse_term("管制二年，剥夺政治权利一年").months == 24


def test_probation_is_ignored():
    assert parse_term("有期徒刑一年六个月，缓刑二年").months == 18


def test_prefix_text_is_tolerated():
    assert parse_term("决定执行有期徒刑五年").months == 60


@pytest.mark.parametrize("text", ["", "   ", "罚金五千元", "有期徒刑"])
def test_invalid_terms_raise(text):
    with pytest.raises(TermParseError):
        parse_term(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("三", 3), ("十", 10), ("十五", 15), ("二十五", 25), ("一百", 100), ("2023", 2023)],
)
def test_cn_number_to_int(text, expected):
    assert cn_number_to_int(text) == expected
