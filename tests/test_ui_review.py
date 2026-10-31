"""界面视图模型的测试。

这一层的价值在于「说什么话」：法院用户看到的每一句都由这里生成，
所以文案与状态判定都要能被测住。tkinter 不参与，测试不需要图形环境。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cedas.domain.models import Crime, Defendant, ProseParagraph, Provenance
from cedas.pipeline import DocumentItem, PipelineReport, PreparedDocument
from cedas.ui import review


def _doc(*, with_id: bool = True) -> PreparedDocument:
    from cedas.domain.models import Case

    case = Case()
    case.case_no.set("（2023）京0105刑初123号", provenance=Provenance(0, "案号"))
    case.court_name.set("北京市朝阳区人民法院", provenance=Provenance(1, "法院"))
    case.judgment_date.set(date(2023, 5, 10), provenance=Provenance(16, "落款"))

    defendant = Defendant(seq=1)
    defendant.name.set("张三", provenance=Provenance(4, "被告人张三"))
    defendant.gender.set("男", provenance=Provenance(4, "男"))
    defendant.birth_date.set(date(1985, 3, 2), provenance=Provenance(4, "1985年3月2日"))
    if with_id:
        defendant.id_number.set("110101198503021234", provenance=Provenance(4, "身份证"))
    crime = Crime()
    crime.charge.set("盗窃", provenance=Provenance(12, "犯盗窃罪"))
    defendant.crimes.append(crime)
    defendant.merged_type.set("有期徒刑")
    defendant.merged_months.set(42)
    defendant.span.start = date(2023, 3, 1)
    defendant.span.end = date(2026, 8, 31)

    paragraphs = [ProseParagraph(index=i, text=f"第{i + 1}段正文") for i in range(18)]
    return PreparedDocument(
        source=Path("判决书.pdf"),
        channel="pdf",
        paragraphs=paragraphs,
        case=case,
        defendants=[defendant],
    )


def _report(*items: DocumentItem, template: Path | None = Path("t.docx")) -> PipelineReport:
    return PipelineReport(
        source=Path("判决书.pdf"),
        channel="pdf",
        doc_type="执行通知书",
        case_no="（2023）京0105刑初123号",
        court_name="北京市朝阳区人民法院",
        paragraph_count=18,
        mapping=Path("m.json"),
        template=template,
        items=list(items),
    )


def _item(*, blockers: list[str] | None = None, rendered: bool = False) -> DocumentItem:
    item = DocumentItem(seq=1, defendant="张三")
    item.blockers = blockers or []
    item.rendered = rendered
    item.start = date(2023, 3, 1)
    item.end = date(2026, 8, 31)
    return item


# ---------------------------------------------------------------------------
# 字段表
# ---------------------------------------------------------------------------


def test_fields_cover_the_four_groups_in_order():
    document = _doc()
    rows = review.build_fields(document, document.defendants[0])
    assert [name for name, _ in review.grouped(rows)] == list(review.GROUP_ORDER)


def test_required_field_without_value_shows_unfilled_label():
    document = _doc(with_id=False)
    rows = {row.label: row for row in review.build_fields(document, document.defendants[0])}
    row = rows["公民身份号码"]

    assert row.required is True
    assert review.display_value(row) == "未录入"


def test_optional_field_without_value_shows_dash_not_a_prompt():
    """选填空着显示「—」。若也写「未录入」，每个空格子都像在催人填。"""
    document = _doc()
    rows = {row.label: row for row in review.build_fields(document, document.defendants[0])}
    assert review.display_value(rows["案由"]) == "—"


def test_effective_date_explains_what_happens_if_left_blank():
    document = _doc()
    rows = {row.label: row for row in review.build_fields(document, document.defendants[0])}
    text = review.display_value(rows["生效日期"])
    assert "留空" in text


def test_start_date_note_is_spoken_language():
    """「按 3 月 1 日拘留起算」而不是「羁押起始日」这种表头话。"""
    document = _doc()
    defendant = document.defendants[0]
    from cedas.domain.enums import CustodyType
    from cedas.domain.models import CustodySegment

    defendant.custody.append(CustodySegment(type=CustodyType.DETENTION, start=date(2023, 3, 1)))
    rows = {row.label: row for row in review.build_fields(document, defendant, start_source="羁押起始日")}

    assert rows["起算依据"].value == "按 3 月 1 日拘留起算"


def test_provenance_points_at_a_paragraph_not_a_page_for_txt():
    document = _doc()
    document.channel = "txt"
    for paragraph in document.paragraphs:
        paragraph.page = None
    rows = {row.label: row for row in review.build_fields(document, document.defendants[0])}
    provenance = rows["姓名"].provenance

    assert provenance.startswith("原文第")
    assert "页" not in provenance, "TXT 通道没有页码，界面不能显示「第 N 页」"


def test_computed_fields_have_no_provenance():
    document = _doc()
    rows = {row.label: row for row in review.build_fields(document, document.defendants[0])}
    assert rows["刑期"].provenance == ""
    assert rows["届满日"].provenance == ""


# ---------------------------------------------------------------------------
# 待办与结论
# ---------------------------------------------------------------------------


def test_blocked_items_sort_before_ready_ones():
    blocked = DocumentItem(seq=2, defendant="李四")
    blocked.blockers = ["E-ID 公民身份号码缺失"]
    ready = DocumentItem(seq=1, defendant="张三")

    rows = review.build_queue(_report(ready, blocked))

    assert [row.title for row in rows] == ["李四", "张三"]
    assert rows[0].status == review.STATUS_BLOCKED
    assert rows[1].status == review.STATUS_READY


def test_blocker_code_is_translated_and_codes_never_reach_the_screen():
    assert review.blocker_hint("E-ID 公民身份号码缺失") == "缺公民身份号码"
    assert review.blocker_hint("E-START 无起算日（判决书未载明）").startswith("还缺起算日")
    # 认不出的码也不能把码本身漏到界面上
    assert review.blocker_hint("E-WHATEVER 某个新错误") == "有必填项没填"
    assert review.blocker_code("E-START 无起算日") == "E-START"


def test_summary_names_the_single_missing_item_in_one_sentence():
    summary = review.summarize(_report(_item(blockers=["E-ID 公民身份号码缺失"])))

    assert summary.headline == "张三这一份只差公民身份号码。"
    assert summary.can_generate is False
    assert summary.action == "补齐后再生成"


def test_summary_counts_when_several_items_missing():
    summary = review.summarize(
        _report(_item(blockers=["E-ID 公民身份号码缺失", "E-BIRTH 出生日期缺失"]))
    )
    assert "还差 2 项" in summary.headline


def test_summary_is_ready_when_nothing_is_missing():
    summary = review.summarize(_report(_item()))
    assert summary.can_generate is True
    assert summary.action == "生成执行通知书"


def test_missing_template_blocks_the_button_with_an_actionable_sentence():
    report = _report(_item(), template=None)
    summary = review.summarize(report, missing_template=review.missing_template(report))

    assert summary.can_generate is False
    assert "模板" in summary.headline
    assert review.missing_template(report) is True


def test_summary_without_defendants_does_not_promise_anything():
    summary = review.summarize(_report())
    assert summary.can_generate is False
    assert "没有认出被告人" in summary.headline


def test_rendered_items_are_reported_as_done():
    item = _item(rendered=True)
    item.path = Path("out/（2023）京0105刑初123号/张三/文书.docx")
    rows = review.build_queue(_report(item))

    assert rows[0].status == review.STATUS_DONE


# ---------------------------------------------------------------------------
# 补录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["2023-05-21", "2023-5-21", "2023年5月21日", "2023/5/21", " 2023-05-21 "]
)
def test_parse_date_accepts_the_ways_people_actually_type(raw):
    assert review.parse_date(raw) == date(2023, 5, 21)


def test_parse_date_rejects_rather_than_guesses():
    with pytest.raises(review.EditError):
        review.parse_date("5月21日")
    with pytest.raises(review.EditError):
        review.parse_date("2023-02-30")


def test_edit_writes_back_and_keeps_the_old_value():
    document = _doc()
    review.apply_edit(document, 1, "defendant.id_number", "110101198503029999")

    field_value = document.defendants[0].id_number
    assert field_value.value == "110101198503029999"
    assert field_value.corrections[0][0] == "110101198503021234", "原值要留痕"


def test_edit_parses_date_fields():
    document = _doc()
    review.apply_edit(document, 1, "case.effective_date", "2023年5月21日")
    assert document.case.effective_date.value == date(2023, 5, 21)


def test_edit_rejects_gender_other_than_two_values():
    document = _doc()
    with pytest.raises(review.EditError):
        review.apply_edit(document, 1, "defendant.gender", "不详")


def test_edit_rejects_read_only_fields():
    document = _doc()
    with pytest.raises(review.EditError):
        review.apply_edit(document, 1, "defendant.crimes", "抢劫")


def test_edit_can_clear_a_field():
    document = _doc()
    review.apply_edit(document, 1, "defendant.id_number", "  ")
    assert document.defendants[0].id_number.value is None


def test_edit_text_gives_iso_dates_for_editing():
    document = _doc()
    defendant = document.defendants[0]
    assert review.edit_text(document, defendant, "defendant.birth_date") == "1985-03-02"


# ---------------------------------------------------------------------------
# 原文面板
# ---------------------------------------------------------------------------


def test_paragraph_numbers_start_at_one():
    document = _doc()
    lines = review.source_lines(document)
    assert lines[0][0] == 1
    assert review.paragraph_text(document, 4) == "第5段正文"


def test_paragraph_text_out_of_range_is_empty_not_an_exception():
    document = _doc()
    assert review.paragraph_text(document, None) == ""
    assert review.paragraph_text(document, 999) == ""
