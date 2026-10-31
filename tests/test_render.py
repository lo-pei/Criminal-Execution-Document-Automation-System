"""渲染层：格式化与模板映射。"""

from __future__ import annotations

from datetime import date

from cedas.domain.models import Case, Crime, Defendant
from cedas.render.formatting import format_cn_date, months_to_chinese
from cedas.render.mapping import Mapping, build_context, resolve


def test_format_cn_date():
    assert format_cn_date(date(2023, 5, 1)) == "2023年5月1日"


def test_format_cn_date_none_returns_empty():
    assert format_cn_date(None) == ""


def test_months_to_chinese():
    assert months_to_chinese(42) == "三年六个月"
    assert months_to_chinese(120) == "十年"
    assert months_to_chinese(10) == "十个月"
    assert months_to_chinese(7) == "七个月"
    assert months_to_chinese(12) == "一年"


def test_months_to_chinese_invalid():
    assert months_to_chinese(None) == ""
    assert months_to_chinese(0) == ""


def _make_case() -> tuple[Case, Defendant]:
    case = Case()
    case.case_no.set("（2023）京0105刑初123号")
    case.effective_date.set(date(2023, 5, 11))

    defendant = Defendant(seq=1)
    defendant.name.set("张三")
    defendant.gender.set("男")
    defendant.merged_months.set(42)

    crime = Crime()
    crime.charge.set("盗窃罪")
    defendant.crimes.append(crime)
    return case, defendant


def test_resolve_handles_list_index():
    case, defendant = _make_case()
    assert resolve({"case": case, "defendant": defendant}, "defendant.crimes.0.charge") == "盗窃罪"


def test_resolve_out_of_range_returns_none():
    _, defendant = _make_case()
    assert resolve({"defendant": defendant}, "defendant.crimes.5.charge") is None


def test_build_context_produces_strings():
    case, defendant = _make_case()
    mapping = Mapping(
        document_type="执行通知书",
        template="执行通知书.docx",
        fields={
            "案号": "case.case_no",
            "被告人": "defendant.name",
            "罪名": "defendant.crimes.0.charge",
            "刑期": "defendant.merged_term_cn",
            "生效日期": "case.effective_date",
            "住址": "defendant.domicile",
        },
    )
    context = build_context(case, defendant, mapping)

    assert context["案号"] == "（2023）京0105刑初123号"
    assert context["罪名"] == "盗窃罪"
    assert context["刑期"] == "三年六个月"
    assert context["生效日期"] == "2023年5月11日"
    assert context["住址"] == "", "缺失字段必须填空串，不能是 None"


def test_placeholder_regex_matches_docx_syntax():
    from cedas.render.template import PLACEHOLDER_RE

    found = PLACEHOLDER_RE.findall("被告人 {{ 被告人 }}，案号 {{案号}}")
    assert found == ["被告人", "案号"]
