"""计算层：刑期解析、日期运算、一致性校验。

本层不 import ``ingest``——计算层不认识文本，只认识数值与日期。
"""

from .consistency import check, verify_span
from .sentence import (
    add_months,
    compute_end_date,
    compute_release_date,
    compute_span,
    offset_days,
    resolve_start,
)
from .term_parser import ParsedTerm, TermParseError, cn_number_to_int, parse_term

__all__ = [
    "ParsedTerm",
    "TermParseError",
    "add_months",
    "check",
    "cn_number_to_int",
    "compute_end_date",
    "compute_release_date",
    "compute_span",
    "offset_days",
    "parse_term",
    "resolve_start",
    "verify_span",
]
