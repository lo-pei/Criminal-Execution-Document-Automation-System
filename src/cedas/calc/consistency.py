"""刑期一致性校验。

判决书正文可能载明「刑期自 X 年 X 月 X 日起至 X 年 X 月 X 日止」。
该表述只作**校验输入**，不作取值来源（ADR 与 spec §3）。

不一致时一律告警交人工裁定，**系统不得自行选择任一方**。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ..domain.enums import Consistency
from ..domain.models import ExtractionWarning, SentenceSpan


def verify_span(
    span: SentenceSpan,
    text_start: Optional[date],
    text_end: Optional[date],
) -> Consistency:
    """比对计算区间与文本载明的起止日。"""
    if text_end is None:
        return Consistency.NO_TEXT_END
    if span.start is None or span.end is None:
        return Consistency.NOT_APPLICABLE
    if span.start == text_start and span.end == text_end:
        return Consistency.PASS
    return Consistency.MISMATCH


def check(
    span: SentenceSpan,
    text_start: Optional[date],
    text_end: Optional[date],
    *,
    defendant_label: str = "",
) -> tuple[SentenceSpan, list[ExtractionWarning]]:
    """执行校验并把结论写回 span，同时产出告警。

    Returns:
        (更新了 consistency 的 span, 告警列表)
    """
    status = verify_span(span, text_start, text_end)
    span.consistency = status

    warnings: list[ExtractionWarning] = []
    if status is Consistency.MISMATCH:
        delta = ""
        if span.end is not None and text_end is not None:
            delta = f"，相差 {(span.end - text_end).days} 天"
        warnings.append(
            ExtractionWarning(
                code="V1",
                message=(
                    f"{defendant_label}刑期计算值与文本载明不一致"
                    f"（计算 {span.start} 至 {span.end}；文本 {text_start} 至 {text_end}{delta}）"
                ),
                field="sentence_end",
            )
        )
    return span, warnings


__all__ = ["check", "verify_span"]
