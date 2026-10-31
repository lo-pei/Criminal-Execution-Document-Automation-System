"""锚点定位与规则抽取测试。"""

from __future__ import annotations

import json

import pytest

from cedas.domain.enums import ParagraphKind
from cedas.extract.anchors import AnchorNotFoundError, locate
from cedas.extract.extractor import extract
from cedas.ingest.cleaner import clean, clean_text


@pytest.fixture
def paragraphs(sample_blocks):
    return clean(sample_blocks)


def test_locates_three_sections(paragraphs):
    anchors = locate(paragraphs)
    body_texts = [p.text for p in paragraphs[anchors.body]]

    assert any("被告人张三犯盗窃罪" in text for text in body_texts)
    assert not any("如不服本判决" in text for text in body_texts)
    assert not any(text == "刑事判决书" for text in body_texts)


def test_tail_contains_signature(paragraphs):
    anchors = locate(paragraphs)
    tail_texts = "".join(p.text for p in paragraphs[anchors.tail])

    assert "如不服本判决" in tail_texts
    assert "审判长" in tail_texts


def test_missing_anchor_raises():
    paragraphs = clean_text("这里没有任何主文锚点。\n被告人张三，男。")
    with pytest.raises(AnchorNotFoundError):
        locate(paragraphs)


def test_extract_case_no(paragraphs):
    result = extract(paragraphs)

    assert result.case.case_no.is_present
    assert "京0105刑初123号" in str(result.case.case_no.value)
    assert result.case.case_no.provenance is not None
    assert result.case.case_no.provenance.page is None, "TXT 通道无页码"
    assert result.case.case_no.extractor.startswith("head_case_no@")


def test_extract_court_name(paragraphs):
    result = extract(paragraphs)
    assert result.case.court_name.value == "北京市朝阳区人民法院"


def test_extraction_result_is_json_serialisable(paragraphs):
    result = extract(paragraphs)
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    restored = json.loads(payload)

    assert restored["case"]["case_no"]["value"]
    assert restored["has_layout_info"] is False
    assert restored["rules"]["head_case_no"] == 1


def test_missing_core_field_produces_warning():
    paragraphs = clean_text("某某人民法院\n刑事判决书\n被告人张三，男。\n判决如下：\n一、判处有期徒刑一年。")
    result = extract(paragraphs)

    codes = {warning.code for warning in result.warnings}
    assert "A003" in codes, "案号缺失应告警"


def test_rule_missing_evidence_is_rejected(tmp_path):
    from cedas.extract.rule_loader import RuleSchemaError, load_rules

    payload = {
        "rule_id": "bad",
        "field": "case_no",
        "layer": "head",
        "pattern": "x",
        "version": 1,
        "evidence": "   ",
        "updated_at": "2026-10-31",
    }
    (tmp_path / "bad.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleSchemaError):
        load_rules(tmp_path)


def test_rule_with_wrong_schema_version_is_rejected(tmp_path):
    from cedas.extract.rule_loader import RuleSchemaError, load_rules

    payload = {
        "schema": 99,
        "rule_id": "future",
        "field": "case_no",
        "layer": "head",
        "pattern": "x",
        "version": 1,
        "evidence": "某样本",
        "updated_at": "2026-10-31",
    }
    (tmp_path / "future.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuleSchemaError):
        load_rules(tmp_path)


def test_case_no_rule_covers_appeal_cases(paragraphs):
    """二审（刑终）判决同样进入执行程序，规则不得只认刑初。"""
    text = "（2023）京03刑终456号\n被告人李四，男。\n判决如下：\n一、驳回上诉。"
    result = extract(clean_text(text))
    assert result.case.case_no.value == "（2023）京03刑终456号"


def test_paragraph_kind_of_case_no_is_set(paragraphs):
    case_no_paragraphs = [p for p in paragraphs if p.kind is ParagraphKind.CASE_NO]
    assert case_no_paragraphs, "案号段落应被单独分类"
