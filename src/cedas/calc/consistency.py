"""刑期一致性校验。

判决书正文常载明「即自 X 年 X 月 X 日起至 X 年 X 月 X 日止」。该表述只作**校验输入**，
不作取值来源（ADR-017）：载明值等于「起算日 + 刑期 − 羁押折抵 − 已执行刑期」，而这两项
都不是从首部读得出来的。拿推算值覆盖它，等于用一条规则盖住另一条；拿它覆盖推算值，
则会让一次抽取出错直接变成一份文书出错。

不一致时一律告警交人工裁定，**系统不自行选择任一方**。真实判决里两种偏差都存在：
载明值更短多为羁押折抵，更长则通常是起算口径不同（如撤销缓刑并罚按本判决执行之日起算）。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ..domain.enums import Consistency
from ..domain.models import ExtractionWarning, SentenceSpan
from .sentence import compute_end_date


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


def implied_offset(
    *,
    months: Optional[int],
    days: int,
    text_start: Optional[date],
    text_end: Optional[date],
) -> Optional[int]:
    """载明区间比「载明起日 + 刑期 − 1 日」短几天。正数即疑似折抵或已执行刑期。

    只在起止日与刑期都齐全时回答，认不出返回 ``None``，不猜。
    这个数不进任何计算，只用来把「差几天」写进告警：人看到 5 天、22 天这样的数字，
    比看到「不一致」三个字更容易想起那是折抵。
    """
    if text_start is None or text_end is None or months is None or months <= 0:
        return None
    try:
        expected = compute_end_date(text_start, months, days)
    except ValueError:
        return None
    return (expected - text_end).days


def check(
    span: SentenceSpan,
    text_start: Optional[date],
    text_end: Optional[date],
    *,
    months: Optional[int] = None,
    days: int = 0,
    defendant_label: str = "",
) -> tuple[SentenceSpan, list[ExtractionWarning]]:
    """执行校验并把结论写回 span，同时产出告警。

    Args:
        span: 系统按「起算日 + 刑期」算出的区间。
        text_start / text_end: 判决书载明的起止日，未载明为 ``None``。
        months / days: 刑期，仅用于算「载明区间差几天」，不参与判定。

    Returns:
        (更新了 ``consistency`` 的 span, 告警列表)
    """
    status = verify_span(span, text_start, text_end)
    span.consistency = status

    warnings: list[ExtractionWarning] = []

    if status is Consistency.MISMATCH and span.end is not None and text_end is not None:
        delta = (span.end - text_end).days
        # 说「晚 5 天 / 早 4 天」，不说「相差 -4 天」：负数要人自己在脑子里翻一次，
        # 而复核的人正在判断的是「该信哪一个」，不该再去算正负。
        leaning = f"比载明值{'晚' if delta > 0 else '早'} {abs(delta)} 天"
        warnings.append(
            ExtractionWarning(
                code="V1",
                message=(
                    f"{defendant_label}刑期计算值与判决书载明不一致"
                    f"（计算 {span.start} 至 {span.end}；载明 {text_start} 至 {text_end}，"
                    f"计算值{leaning}）。"
                    "多为羁押折抵或起算口径不同，请核对原文后确定采哪一个"
                ),
                field="sentence_end",
            )
        )
    elif status is Consistency.NOT_APPLICABLE:
        # 起算日还没有（并罚案件待人工填，或未录入生效日期）。这时比不了首尾，
        # 但载明区间本身的长短仍能比：差 22 天往往正对应判决书里那句「共折抵二十二日」，
        # 承办人填起算日时据此判断「只是折抵」还是「载明值与刑期本身不符」。
        gap = implied_offset(
            months=months, days=days, text_start=text_start, text_end=text_end
        )
        if gap:
            shorter = "短" if gap > 0 else "长"
            warnings.append(
                ExtractionWarning(
                    code="V2",
                    message=(
                        f"{defendant_label}判决书载明的刑期区间（{text_start} 至 {text_end}）"
                        f"比「起算日 + 刑期 − 1 日」{shorter} {abs(gap)} 天，"
                        "填写起算日时请核对是否为羁押折抵或已执行刑期扣减"
                    ),
                    field="sentence_start",
                )
            )

    return span, warnings


__all__ = ["check", "implied_offset", "verify_span"]
