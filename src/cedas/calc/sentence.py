"""刑期日期计算。

三条规则，全部来自法定条文，不依赖样本：

1. 届满日 = 起算日 + 刑期 − 1 日（ADR-006）。刑期含起算日当日。
2. 折抵：管制 1:2，拘役与有期徒刑 1:1，无期与死刑不折抵。
3. 释放日 = 届满日 + 1 日。

**起算日约定（易错，务必按此实现）**：

- 有期徒刑 / 拘役：起算日 = 羁押起始日。羁押期已包含在刑期内，
  因此**不再另行扣减羁押天数**——否则会重复折抵。
- 管制：起算日 = 判决执行之日；羁押折抵按 1:2 从届满日向前扣减。
- 无期徒刑 / 死刑：无起止日。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

from ..domain.enums import Consistency, PrincipalType
from ..domain.models import SentenceSpan


def add_months(start: date, months: int) -> date:
    """按日历推进月数；目标月无对应日时取该月最后一日。"""
    if months < 0:
        raise ValueError("月数不得为负")

    total = start.year * 12 + (start.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1

    last_day = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day)
    return date(year, month, day)


def compute_end_date(start: date, months: int) -> date:
    """计算刑期届满日。

    届满日 = 起算日 + 刑期 − 1 日。

    >>> compute_end_date(date(2023, 1, 1), 48).isoformat()
    '2026-12-31'
    """
    if months <= 0:
        raise ValueError("刑期月数必须为正")
    return add_months(start, months) - timedelta(days=1)


def compute_release_date(end: date) -> date:
    """释放日为届满日次日。"""
    return end + timedelta(days=1)


def offset_days(principal_type: PrincipalType, custody_days: int) -> int:
    """折算羁押可折抵的刑期天数。

    注意：本函数只回答「折抵多少天」，是否需要在届满日上扣减由
    :func:`compute_span` 按主刑种类决定，避免重复扣减。
    """
    if custody_days < 0:
        raise ValueError("羁押天数不得为负")

    if principal_type is PrincipalType.CONTROL:
        return custody_days * 2
    if principal_type in (PrincipalType.DETENTION, PrincipalType.FIXED_TERM):
        return custody_days
    return 0


def resolve_start(
    principal_type: PrincipalType,
    *,
    custody_start: Optional[date] = None,
    effective_date: Optional[date] = None,
) -> Optional[date]:
    """按主刑种类确定刑期起算日。"""
    if principal_type in (PrincipalType.DETENTION, PrincipalType.FIXED_TERM):
        # 在押：自羁押之日起算；未在押：自判决执行（生效）之日起算
        return custody_start or effective_date
    if principal_type is PrincipalType.CONTROL:
        return effective_date or custody_start
    return None


def compute_span(
    *,
    principal_type: PrincipalType,
    months: Optional[int],
    start: Optional[date],
    custody_days: int = 0,
) -> SentenceSpan:
    """计算完整刑期区间。

    输入不全时返回 ``consistency = NOT_APPLICABLE`` 的空区间，不抛异常——
    缺失是常态（未在押案件无羁押日），由上层决定是否阻断。
    """
    if (
        principal_type not in (PrincipalType.CONTROL, PrincipalType.DETENTION, PrincipalType.FIXED_TERM)
        or months is None
        or months <= 0
        or start is None
    ):
        return SentenceSpan(months=months, consistency=Consistency.NOT_APPLICABLE)

    end = compute_end_date(start, months)
    applied = offset_days(principal_type, custody_days)

    # 有期徒刑/拘役的羁押期已含在起算日里，不能再扣；只有管制需要额外扣减
    if principal_type is PrincipalType.CONTROL and applied:
        end = end - timedelta(days=applied)

    return SentenceSpan(
        start=start,
        end=end,
        months=months,
        offset_days=applied,
        consistency=Consistency.NOT_APPLICABLE,
    )


__all__ = [
    "add_months",
    "compute_end_date",
    "compute_release_date",
    "compute_span",
    "offset_days",
    "resolve_start",
]
