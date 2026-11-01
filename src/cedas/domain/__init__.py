"""领域层：数据模型与枚举。

本层零外部依赖，只用标准库。任何对上层模块的 import 均属设计缺陷。
"""

from .enums import (
    AdditionalType,
    CombinationKind,
    Consistency,
    CustodyType,
    FieldStatus,
    Gender,
    ParagraphKind,
    PrincipalType,
)
from .models import (
    AdditionalPunishment,
    Case,
    Crime,
    CustodySegment,
    Defendant,
    ExtractionWarning,
    FieldValue,
    Probation,
    ProseParagraph,
    Provenance,
    SentenceSpan,
    blank,
    build_field,
)

__all__ = [
    "AdditionalPunishment",
    "AdditionalType",
    "Case",
    "CombinationKind",
    "Consistency",
    "Crime",
    "CustodySegment",
    "CustodyType",
    "Defendant",
    "ExtractionWarning",
    "FieldStatus",
    "FieldValue",
    "Gender",
    "ParagraphKind",
    "PrincipalType",
    "Probation",
    "ProseParagraph",
    "Provenance",
    "SentenceSpan",
    "blank",
    "build_field",
]
