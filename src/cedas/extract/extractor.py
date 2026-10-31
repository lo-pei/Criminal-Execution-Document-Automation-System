"""抽取层：锚点定位、规则加载、字段字典装配。

本层不做日期运算（交给 calc），不渲染文书（交给 render）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..config import RULES_DIR
from ..domain.models import Case, ExtractionWarning, FieldValue, ProseParagraph
from .anchors import AnchorNotFoundError, Anchors, locate
from .rule_loader import Rule, load_rules, rule_versions

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
    warnings: list[ExtractionWarning] = field(default_factory=list)
    rules: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": {name: _field_to_dict(getattr(self.case, name)) for name in CASE_FIELDS.values()},
            "defendants": [],
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
    rules_dir: Optional[Any] = None,
) -> ExtractionResult:
    """执行完整抽取。

    锚点定位失败时不抛异常，转为告警——上层据此阻断生成，但保留清洗结果供排查。
    """
    rules = list(rules) if rules is not None else load_rules(rules_dir or RULES_DIR)

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

    for name in ("case_no", "court_name"):
        if not getattr(result.case, name).is_present:
            result.warnings.append(
                ExtractionWarning(code="A003", message=f"核心字段缺失：{name}", field=name)
            )

    return result


__all__ = ["CASE_FIELDS", "ExtractionResult", "extract"]
