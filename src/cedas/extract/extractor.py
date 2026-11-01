"""抽取层：锚点定位、规则加载、字段字典装配。

本层不做日期运算（交给 calc），不渲染文书（交给 render）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..calc.consistency import check as check_consistency
from ..calc.sentence import compute_span
from ..config import rule_search_dirs
from ..domain.enums import PrincipalType
from ..domain.models import (
    Case,
    Defendant,
    ExtractionWarning,
    FieldValue,
    ProseParagraph,
)
from .anchors import AnchorNotFoundError, Anchors, locate
from .case_info import extract_case_info
from .defendant import attach_to_case, extract_defendants
from .rule_loader import Rule, load_rules_from, rule_versions

# 规则字段 → Case 属性名。新增字段时在此登记，未登记字段会产出告警而非静默丢弃。
CASE_FIELDS: dict[str, str] = {
    "case_no": "case_no",
    "court_name": "court_name",
    "prosecutor_organ": "prosecutor_organ",
    "judgment_date": "judgment_date",
    "panel": "panel",
    "clerk": "clerk",
}


@dataclass
class ExtractionResult:
    """一次抽取的完整产物。"""

    paragraphs: list[ProseParagraph]
    anchors: Optional[Anchors]
    case: Case = field(default_factory=Case)
    defendants: list[Defendant] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    rules: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": {name: _field_to_dict(getattr(self.case, name)) for name in CASE_FIELDS.values()},
            "defendants": [_defendant_to_dict(d) for d in self.defendants],
            "warnings": [
                {"code": w.code, "message": w.message, "field": w.field} for w in self.warnings
            ],
            "rules": self.rules,
            "paragraph_count": len(self.paragraphs),
            "has_layout_info": any(p.page is not None for p in self.paragraphs),
        }


def _field_to_dict(fv: FieldValue) -> dict[str, Any]:
    value = fv.value
    if isinstance(value, date):
        value = value.isoformat()
    return {
        "value": value,
        "status": fv.status.value,
        "confidence": fv.confidence,
        "extractor": fv.extractor,
        "missing_reason": fv.missing_reason,
        "provenance": None
        if fv.provenance is None
        else {
            "page": fv.provenance.page,
            "paragraph_index": fv.provenance.paragraph_index,
            "location": fv.provenance.display_location(),
            "snippet": fv.provenance.snippet,
        },
    }


_PRINCIPAL_BY_VALUE = {member.value: member for member in PrincipalType}


def _attach_spans(defendants: Sequence[Defendant]) -> list[ExtractionWarning]:
    """按主刑种类与月数计算刑期区间，并与判决书载明的起止日比对。

    这里只**调用** calc，不做日期运算——起点取最早羁押日，未在押时为空，
    由复核界面补录入（字段字典 C 组）。

    校验结果一并返回：``cedas extract`` 的输出与 ``tools/sample_report.py`` 的摘要
    是采集样本、排查缺陷时唯一的观察窗口，让不一致只出现在 render 阶段，
    等于把问题藏到最后一公里。
    """
    warnings: list[ExtractionWarning] = []
    for defendant in defendants:
        principal = _PRINCIPAL_BY_VALUE.get(str(defendant.merged_type.value), PrincipalType.UNKNOWN)
        months = defendant.merged_months.value
        months = months if isinstance(months, int) else None
        span = compute_span(
            principal_type=principal,
            months=months,
            days=defendant.merged_days,
            start=defendant.custody_start,
        )
        defendant.span, produced = check_consistency(
            span,
            *defendant.declared_span,
            months=months,
            days=defendant.merged_days,
            defendant_label=defendant.display_name,
        )
        warnings.extend(produced)
    return warnings


def _defendant_to_dict(defendant: Defendant) -> dict[str, Any]:
    """被告人序列化。日期统一转 ISO，界面与下游只认一种格式。"""
    span = defendant.span
    declared = defendant.declared_span

    def iso(value):
        return value.isoformat() if isinstance(value, date) else value

    return {
        "seq": defendant.seq,
        "name": _field_to_dict(defendant.name),
        "gender": _field_to_dict(defendant.gender),
        "birth_date": _field_to_dict(defendant.birth_date),
        "id_number": _field_to_dict(defendant.id_number),
        "ethnicity": _field_to_dict(defendant.ethnicity),
        "domicile": _field_to_dict(defendant.domicile),
        "education": _field_to_dict(defendant.education),
        "detention_place": _field_to_dict(defendant.detention_place),
        "custody": [
            {"type": seg.type.value, "start": iso(seg.start), "end": iso(seg.end)}
            for seg in defendant.custody
        ],
        "crimes": [
            {
                "charge": _field_to_dict(crime.charge),
                "principal_type": _field_to_dict(crime.principal_type),
                "term_months": _field_to_dict(crime.term_months),
                "additional": [
                    {
                        "type": extra.type.value,
                        "fine_amount": extra.fine_amount,
                        "raw_text": extra.raw_text,
                    }
                    for extra in crime.additional
                ],
            }
            for crime in defendant.crimes
        ],
        "merged_type": _field_to_dict(defendant.merged_type),
        "merged_months": _field_to_dict(defendant.merged_months),
        "is_combined": defendant.is_combined,
        "combination": defendant.combination.value,
        "exempt": _field_to_dict(defendant.exempt),
        "verdict_absent": _field_to_dict(defendant.verdict_absent),
        "probation": {
            "applied": _field_to_dict(defendant.probation.applied),
            "months": _field_to_dict(defendant.probation.months),
        },
        "span": {
            "start": iso(span.start),
            "end": iso(span.end),
            "months": span.months,
            "consistency": span.consistency.value,
            # 判决书载明的起止日。与上两项并列而非合并：一致性校验要能一眼看出
            # 「算出来的」和「抄下来的」分别是多少。
            "declared_start": iso(declared[0]),
            "declared_end": iso(declared[1]),
        },
    }


def _apply_rule(rule: Rule, paragraphs: Sequence[ProseParagraph], section: slice) -> Optional[FieldValue]:
    pattern = rule.compile()
    for paragraph in paragraphs[section]:
        match = pattern.search(paragraph.text)
        if match is None:
            continue

        value = match.group(rule.group) if pattern.groups else match.group(0)
        fv: FieldValue = FieldValue()
        from ..domain.models import Provenance

        return fv.set(
            value.strip(),
            provenance=Provenance(
                paragraph_index=paragraph.index,
                snippet=paragraph.text[:120],
                page=paragraph.page,
            ),
            extractor=f"{rule.rule_id}@v{rule.version}",
            confidence=1.0,
        )
    return None


def extract(
    paragraphs: Sequence[ProseParagraph],
    *,
    rules: Optional[Sequence[Rule]] = None,
    rules_dirs: Optional[Sequence[Any]] = None,
) -> ExtractionResult:
    """执行完整抽取。

    锚点定位失败时不抛异常，转为告警——上层据此阻断生成，但保留清洗结果供排查。
    规则目录未显式指定时走 ``config.rule_search_dirs()``，即基线 + 现场补丁两层。
    """
    rules = (
        list(rules)
        if rules is not None
        else load_rules_from(rules_dirs if rules_dirs is not None else rule_search_dirs())
    )

    result = ExtractionResult(
        paragraphs=list(paragraphs),
        anchors=None,
        rules=rule_versions(rules),
    )

    try:
        anchors = locate(paragraphs)
    except AnchorNotFoundError as exc:
        result.warnings.append(ExtractionWarning(code="A001", message=str(exc), field="body"))
        return result

    result.anchors = anchors

    for rule in rules:
        if rule.field not in CASE_FIELDS:
            result.warnings.append(
                ExtractionWarning(
                    code="A002",
                    message=f"规则 {rule.rule_id} 指向未登记的字段 {rule.field}，已跳过",
                    field=rule.field,
                )
            )
            continue

        section = getattr(anchors, rule.layer)
        found = _apply_rule(rule, paragraphs, section)
        if found is not None:
            setattr(result.case, CASE_FIELDS[rule.field], found)

    result.defendants = extract_defendants(paragraphs, anchors)
    result.warnings.extend(extract_case_info(result.case, paragraphs, result.anchors))
    attach_to_case(result.case, result.defendants)
    result.warnings.extend(_attach_spans(result.defendants))

    # 单位犯罪：对「被告单位」只能判罚金，没有刑期，也就没有执行通知书。
    # 这类判决不会被当成「缺字段」报错，反而容易让人以为漏抽了一个被告人。
    if any("被告单位" in paragraph.text for paragraph in paragraphs[anchors.head]):
        result.warnings.append(
            ExtractionWarning(
                code="A006",
                message="本案含被告单位：单位部分不生成执行通知书，请核对被告人范围",
                field="defendants",
            )
        )

    if not result.defendants:
        result.warnings.append(
            ExtractionWarning(
                code="A004",
                message="未识别到被告人：检查首部是否为「被告人姓名，性别，…出生」句式",
                field="defendants",
            )
        )

    for name in ("case_no", "court_name"):
        if not getattr(result.case, name).is_present:
            result.warnings.append(
                ExtractionWarning(code="A003", message=f"核心字段缺失：{name}", field=name)
            )

    return result


__all__ = ["CASE_FIELDS", "ExtractionResult", "extract"]
