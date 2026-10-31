"""数据模型。

设计原则：
1. 所有标量字段都包成 ``FieldValue``，以便携带溯源与置信度。
2. 层级按「一案多人、一人多罪」建模，罪名是列表而非标量（见 ADR 与 spec §2）。
3. 本模块只依赖标准库，不得引入第三方包（见 02-系统架构.md §1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Generic, Optional, TypeVar

from .enums import (
    AdditionalType,
    Consistency,
    CustodyType,
    FieldStatus,
    Gender,
    ParagraphKind,
)

T = TypeVar("T")


def blank() -> FieldValue[Any]:
    """构造一个空字段值的工厂，供 dataclass 默认值使用。"""
    return FieldValue()


@dataclass
class Provenance:
    """字段溯源。

    ``page`` 为 ``None`` 时表示来源为 TXT 通道（无页码信息），
    复核界面应显示「段落 N」而非「第 N 页」。
    """

    paragraph_index: int
    snippet: str
    page: Optional[int] = None

    def display_location(self) -> str:
        if self.page is None:
            return f"段落 {self.paragraph_index + 1}"
        return f"第 {self.page} 页 · 段落 {self.paragraph_index + 1}"


@dataclass
class FieldValue(Generic[T]):
    """带溯源与状态的字段值。"""

    value: Optional[T] = None
    provenance: Optional[Provenance] = None
    extractor: str = ""
    confidence: float = 1.0
    status: FieldStatus = FieldStatus.MISSING
    missing_reason: Optional[str] = None
    corrections: list[tuple[Optional[T], str]] = field(default_factory=list)

    @property
    def is_present(self) -> bool:
        return self.value is not None and self.value != ""

    def set(
        self,
        value: Optional[T],
        *,
        provenance: Optional[Provenance] = None,
        extractor: str = "",
        confidence: float = 1.0,
    ) -> FieldValue[T]:
        self.value = value
        self.provenance = provenance
        self.extractor = extractor
        self.confidence = confidence
        self.status = FieldStatus.EXTRACTED if value is not None else FieldStatus.MISSING
        return self

    def mark_missing(self, reason: str) -> FieldValue[T]:
        self.value = None
        self.status = FieldStatus.MISSING
        self.missing_reason = reason
        return self

    def correct(self, value: Optional[T], note: str = "") -> FieldValue[T]:
        """人工修正。原值进 corrections，不覆盖历史。"""
        self.corrections.append((self.value, note))
        self.value = value
        self.status = FieldStatus.CORRECTED
        self.missing_reason = None
        return self


@dataclass
class ProseParagraph:
    """清洗后的段落。所有抽取规则的输入单位。"""

    index: int
    text: str
    kind: ParagraphKind = ParagraphKind.BODY
    page: Optional[int] = None
    font_size: Optional[float] = None
    source_lines: tuple[int, int] = (0, 0)

    def __len__(self) -> int:
        return len(self.text)


@dataclass
class AdditionalPunishment:
    """附加刑。一个罪名可同时有多个附加刑，故以列表存放。"""

    type: AdditionalType
    fine_amount: Optional[float] = None
    fine_deadline: Optional[str] = None
    confiscation_all: Optional[bool] = None
    political_rights_years: Optional[int] = None
    raw_text: str = ""


@dataclass
class Crime:
    """单项罪名及其刑罚。数罪并罚时列表长度 > 1。"""

    charge: FieldValue = field(default_factory=blank)
    principal_type: FieldValue = field(default_factory=blank)
    term_months: FieldValue = field(default_factory=blank)
    additional: list[AdditionalPunishment] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class CustodySegment:
    """一段强制措施。同一被告人可能有多段（拘留 → 逮捕），必须存为有序列表。"""

    type: CustodyType
    organ: Optional[str] = None
    start: Optional[date] = None
    end: Optional[date] = None

    @property
    def ongoing(self) -> bool:
        return self.end is None


@dataclass
class SentenceSpan:
    """刑期区间。由 calc 层计算得出，非抽取所得。"""

    start: Optional[date] = None
    end: Optional[date] = None
    months: Optional[int] = None
    offset_days: int = 0
    consistency: Consistency = Consistency.NOT_APPLICABLE

    @property
    def release_date(self) -> Optional[date]:
        from datetime import timedelta

        return None if self.end is None else self.end + timedelta(days=1)


@dataclass
class Probation:
    applied: FieldValue = field(default_factory=blank)
    months: FieldValue = field(default_factory=blank)


@dataclass
class Defendant:
    """一名被告人。判决书中每出现一名被告人即生成一个实例。"""

    seq: int = 1
    name: FieldValue = field(default_factory=blank)
    gender: FieldValue = field(default_factory=blank)
    birth_date: FieldValue = field(default_factory=blank)
    id_number: FieldValue = field(default_factory=blank)
    ethnicity: FieldValue = field(default_factory=blank)
    domicile: FieldValue = field(default_factory=blank)
    education: FieldValue = field(default_factory=blank)
    occupation: FieldValue = field(default_factory=blank)
    custody: list[CustodySegment] = field(default_factory=list)
    detention_place: FieldValue = field(default_factory=blank)
    prior_record: FieldValue = field(default_factory=blank)

    crimes: list[Crime] = field(default_factory=list)
    merged_type: FieldValue = field(default_factory=blank)
    merged_months: FieldValue = field(default_factory=blank)
    probation: Probation = field(default_factory=Probation)
    recovery: FieldValue = field(default_factory=blank)
    civil_compensation: FieldValue = field(default_factory=blank)

    span: SentenceSpan = field(default_factory=SentenceSpan)

    @property
    def display_name(self) -> str:
        return str(self.name.value) if self.name.is_present else f"被告人{self.seq}"

    @property
    def in_custody(self) -> bool:
        return any(seg.type.deprives_liberty and seg.ongoing for seg in self.custody)

    @property
    def custody_start(self) -> Optional[date]:
        """羁押起始日 = 最早一段羁押性措施的起始日。"""
        starts = [
            seg.start for seg in self.custody if seg.type.deprives_liberty and seg.start is not None
        ]
        return min(starts) if starts else None

    @property
    def is_combined(self) -> bool:
        """是否数罪并罚。"""
        return len(self.crimes) > 1


@dataclass
class Case:
    """一起案件。"""

    case_no: FieldValue = field(default_factory=blank)
    court_name: FieldValue = field(default_factory=blank)
    prosecutor_organ: FieldValue = field(default_factory=blank)
    judgment_date: FieldValue = field(default_factory=blank)
    service_date: FieldValue = field(default_factory=blank)
    effective_date: FieldValue = field(default_factory=blank)
    filing_date: FieldValue = field(default_factory=blank)
    panel: FieldValue = field(default_factory=blank)
    clerk: FieldValue = field(default_factory=blank)
    cause: FieldValue = field(default_factory=blank)

    defendants: list[Defendant] = field(default_factory=list)

    @property
    def primary_defendant(self) -> Optional[Defendant]:
        return self.defendants[0] if self.defendants else None


@dataclass
class ExtractionWarning:
    """抽取与校验告警。命名加下划线避免与内置 Warning 冲突。"""

    code: str
    message: str
    field: Optional[str] = None
    paragraph_index: Optional[int] = None

    def __str__(self) -> str:  # pragma: no cover - 仅日志用
        loc = f"（段落 {self.paragraph_index}）" if self.paragraph_index is not None else ""
        return f"[{self.code}] {self.message}{loc}"


def build_field(value: Any, paragraph: Optional[ProseParagraph] = None, **kwargs: Any) -> FieldValue:
    """便捷构造：从段落直接生成带溯源的 FieldValue。"""
    fv: FieldValue = FieldValue()
    prov = None
    if paragraph is not None:
        prov = Provenance(
            paragraph_index=paragraph.index,
            snippet=paragraph.text[:120],
            page=paragraph.page,
        )
    return fv.set(value, provenance=prov, **kwargs)


__all__ = [
    "AdditionalPunishment",
    "Case",
    "Crime",
    "CustodySegment",
    "Defendant",
    "ExtractionWarning",
    "FieldValue",
    "Gender",
    "Probation",
    "ProseParagraph",
    "Provenance",
    "SentenceSpan",
    "blank",
    "build_field",
]
