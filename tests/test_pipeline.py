"""端到端：判决书 → 抽取 → 刑期 → 模板填充 → 归档 → 台账。

模板由 ``tools/make_demo_template.py`` 现生成，不依赖仓库里是否存在 .docx
（模板属受控材料，不入库）。断言落在**产物内容**上，不是「函数没抛异常」：
执行文书错了没人会当场发现，等送到看守所才发现就晚了。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from cedas.domain.enums import CustodyType, PrincipalType
from cedas.domain.models import Case, CustodySegment, Defendant, SentenceSpan
from cedas.pipeline import check_blockers, generate, resolve_span
from cedas.render import MappingNotFoundError
from cedas.storage.db import Database

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "S000__京0105刑初123号.pdf"
MAPPING = ROOT / "resources" / "mappings" / "执行通知书.example.json"

CASE_NO = "（2023）京0105刑初9号"
WHEN = date(2023, 5, 20)

BASE_LINES = [
    "北京市朝阳区人民法院",
    "刑事判决书",
    CASE_NO,
    "公诉机关北京市朝阳区人民检察院。",
    "被告人李四，男，1990年1月2日出生，公民身份号码110101199001021234，"
    "汉族，初中文化，住北京市朝阳区某路2号。",
    "因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留，同年3月14日被逮捕。",
    "现羁押于北京市朝阳区看守所。",
    "北京市朝阳区人民检察院以京朝检刑诉〔2023〕1号起诉书指控被告人李四犯盗窃罪，"
    "于2023年4月1日向本院提起公诉。",
    "本院依法适用简易程序，实行独任审判，公开开庭审理了本案。",
    "现已审理终结。",
    "本院认为，被告人李四以非法占有为目的，秘密窃取他人财物，数额较大，"
    "其行为已构成盗窃罪，依法应予惩处。",
    "判决如下：",
    "一、被告人李四犯盗窃罪，判处拘役四个月。",
    "如不服本判决，可在接到判决书的第二日起十日内提出上诉。",
    "审判员王五",
    "二〇二三年五月十日",
    "书记员钱七",
]


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """现生成合成模板。"""
    spec = importlib.util.spec_from_file_location(
        "make_demo_template", ROOT / "tools" / "make_demo_template.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(tmp_path_factory.mktemp("tpl") / "执行通知书.示例.docx")


def _judgment(tmp_path: Path, lines: list[str] | None = None) -> Path:
    path = tmp_path / "判决书.txt"
    path.write_text("\n".join(lines or BASE_LINES), encoding="utf-8")
    return path


def _text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    chunks = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            chunks.extend(cell.text for cell in row.cells)
    return "\n".join(chunks)


def test_generate_end_to_end(template, tmp_path):
    report = generate(
        _judgment(tmp_path),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )

    assert report.ok
    assert report.case_no == CASE_NO
    assert report.court_name == "北京市朝阳区人民法院"

    item = report.items[0]
    assert item.defendant == "李四"
    assert item.start_source == "羁押起始日"
    assert item.start == date(2023, 3, 1)
    assert item.end == date(2023, 6, 30), "拘役四个月，届满日 = 起算日 + 4 月 − 1 日"

    assert item.path is not None
    assert item.path.parent.name == "李四"
    assert item.path.name == f"{CASE_NO}_李四_执行通知书_20230520.docx"

    text = _text(item.path)
    assert "李四" in text
    assert "拘役四个月" in text
    assert "2023年3月1日 至 2023年6月30日" in text
    assert "北京市朝阳区看守所" in text
    assert "书记员　钱七" in text
    assert "2023年5月10日" in text, "落款日期取判决日期，不取上诉期那一句"


def test_rendered_document_has_no_placeholder_left(template, tmp_path):
    report = generate(
        _judgment(tmp_path),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )
    text = _text(report.items[0].path)
    assert "{{" not in text and "}}" not in text


def test_second_run_keeps_first_version(template, tmp_path):
    kwargs = dict(
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )
    first = generate(_judgment(tmp_path), **kwargs)
    second = generate(_judgment(tmp_path), **kwargs)

    assert first.items[0].version == 1
    assert second.items[0].version == 2
    assert "_v2." in second.items[0].path.name
    assert first.items[0].path.exists(), "重跑不得覆盖已签发的文书"


def test_dry_run_writes_nothing(template, tmp_path):
    report = generate(
        _judgment(tmp_path),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
        dry_run=True,
    )
    assert report.ok
    assert report.items[0].rendered is False
    assert report.items[0].path is None
    assert not (tmp_path / "out").exists()
    assert report.items[0].context["被告人"] == "李四", "预演也要给出上下文供人工核对"


def test_unknown_document_type_raises(tmp_path):
    with pytest.raises(MappingNotFoundError):
        generate(_judgment(tmp_path), doc_type="结案登记表", record=False)


def test_missing_id_number_blocks_generation(template, tmp_path):
    lines = [line for line in BASE_LINES if "公民身份号码" not in line]
    lines[4] = "被告人李四，男，1990年1月2日出生，汉族，初中文化，住北京市朝阳区某路2号。"

    report = generate(
        _judgment(tmp_path, lines),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )

    assert not report.ok
    assert any("E-ID" in blocker for blocker in report.items[0].blockers)
    assert report.items[0].rendered is False
    assert not list((tmp_path / "out").rglob("*.docx")), "阻断时不得留下残次文书"


def test_allow_missing_produces_degraded_document(template, tmp_path):
    lines = list(BASE_LINES)
    lines[4] = "被告人李四，男，1990年1月2日出生，汉族，初中文化，住北京市朝阳区某路2号。"

    report = generate(
        _judgment(tmp_path, lines),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
        allow_missing=True,
    )

    item = report.items[0]
    assert item.rendered and item.degraded
    assert item.blockers == []
    assert any("W-DEGRADED" in warning and "E-ID" in warning for warning in item.warnings)


def test_start_date_falls_back_to_effective_date(template, tmp_path):
    """未在押（取保候审）且文书未载起止日：按判决生效日起算，并留下痕迹。"""
    lines = list(BASE_LINES)
    lines[5] = "因涉嫌犯盗窃罪，于2023年3月10日被取保候审。"
    lines[6] = "现取保候审于北京市朝阳区某路2号。"

    blocked = generate(
        _judgment(tmp_path, lines),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )
    assert any("E-START" in blocker for blocker in blocked.items[0].blockers)

    report = generate(
        _judgment(tmp_path, lines),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        effective_date=date(2023, 6, 1),
        record=False,
    )

    item = report.items[0]
    assert item.ok
    assert item.start == date(2023, 6, 1)
    assert item.start_source == "生效日期"
    assert any("W-START-EFFECTIVE" in warning for warning in item.warnings)
    assert "2023年6月1日 至 2023年9月30日" in _text(item.path)


def test_records_document_to_ledger(template, tmp_path):
    database = tmp_path / "cedas.db"
    report = generate(
        _judgment(tmp_path),
        output_dir=tmp_path / "out",
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        db_path=database,
    )

    with Database(database) as db:
        cases = db.list_cases()
        assert [row["case_no"] for row in cases] == [CASE_NO]
        assert db.count_documents(cases[0]["id"]) == 1

    assert report.rendered[0].path.exists()


def test_check_blockers_reports_each_missing_field():
    case = Case()
    defendant = Defendant(seq=1)
    blockers = check_blockers(case, defendant, SentenceSpan())
    codes = {blocker.split()[0] for blocker in blockers}

    assert {"E-CASE-NO", "E-COURT", "E-NAME", "E-GENDER", "E-BIRTH", "E-ID"} <= codes
    assert "E-CHARGES" in codes
    assert "E-START" in codes


def test_resolve_span_prefers_custody_for_fixed_term():
    case = Case()
    case.effective_date.set(date(2023, 6, 1))

    defendant = Defendant(seq=1)
    defendant.merged_type.set(PrincipalType.FIXED_TERM.value)
    defendant.merged_months.set(36)
    defendant.custody.append(CustodySegment(type=CustodyType.DETENTION, start=date(2023, 3, 1)))

    span, source = resolve_span(case, defendant)
    assert span.start == date(2023, 3, 1)
    assert source == "羁押起始日"


def test_resolve_span_uses_effective_date_for_control():
    """管制自判决执行之日起算——即使有羁押日也不能拿羁押日起算。"""
    case = Case()
    case.effective_date.set(date(2023, 6, 1))

    defendant = Defendant(seq=1)
    defendant.merged_type.set(PrincipalType.CONTROL.value)
    defendant.merged_months.set(6)
    defendant.custody.append(CustodySegment(type=CustodyType.DETENTION, start=date(2023, 3, 1)))

    span, source = resolve_span(case, defendant)
    assert span.start == date(2023, 6, 1)
    assert source == "生效日期"


def test_manual_start_date_wins():
    case = Case()
    case.effective_date.set(date(2023, 6, 1))

    defendant = Defendant(seq=1)
    defendant.merged_type.set(PrincipalType.FIXED_TERM.value)
    defendant.merged_months.set(12)

    span, source = resolve_span(case, defendant, start_date=date(2023, 7, 1))
    assert span.start == date(2023, 7, 1)
    assert source == "人工指定"


def test_pdf_and_txt_channels_produce_same_document(template, tmp_path):
    """同一份合成文书的 PDF 与 TXT 通道，生成的文书正文必须逐字相同。"""
    kwargs = dict(
        mapping_path=MAPPING,
        template_path=template,
        when=WHEN,
        record=False,
    )
    from_pdf = generate(SAMPLE, output_dir=tmp_path / "pdf", **kwargs)
    from_txt = generate(
        ROOT / "examples" / "S000__京0105刑初123号.txt", output_dir=tmp_path / "txt", **kwargs
    )

    assert _text(from_pdf.items[0].path) == _text(from_txt.items[0].path)


# ---------------------------------------------------------------------------
# prepare / plan：界面要能「只预演、不落盘」
# ---------------------------------------------------------------------------


def _judgment_text(tmp_path: Path) -> Path:
    path = tmp_path / "判决书.txt"
    path.write_text("\n".join(BASE_LINES), encoding="utf-8")
    return path


def test_prepare_reads_without_writing_anything(tmp_path):
    """prepare 只读文件、只抽字段。界面选完文件就调它，不能有副作用。"""
    from cedas.pipeline import prepare

    document = prepare(_judgment_text(tmp_path))

    assert document.channel == "txt"
    assert document.defendants[0].display_name == "李四"
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob("*.docx"))


def test_plan_reports_blockers_without_writing(tmp_path):
    from cedas.pipeline import plan, prepare

    document = prepare(_judgment_text(tmp_path))
    report = plan(document, mapping_path=MAPPING, require_template=False)

    assert report.items[0].context["被告人"] == "李四", "预演要给上下文，人工才能核对"
    assert report.items[0].rendered is False
    assert not (tmp_path / "out").exists()


def test_plan_survives_a_missing_template_and_says_so(tmp_path, monkeypatch):
    """没有模板时界面仍要能打开——否则用户连缺哪个字段都看不到。"""
    from cedas.pipeline import plan, prepare
    from cedas.render import template as template_module
    from cedas.render.template import TemplateNotFoundError

    empty = tmp_path / "空的模板目录"
    empty.mkdir()
    monkeypatch.setattr(template_module, "template_search_dirs", lambda: [empty])

    document = prepare(_judgment_text(tmp_path))
    report = plan(document, mapping_path=MAPPING, require_template=False)

    assert report.template is None
    assert any("模板" in warning for warning in report.warnings)

    with pytest.raises(TemplateNotFoundError):
        plan(document, mapping_path=MAPPING)  # 默认要求模板存在，保持原行为


def test_plan_accepts_per_defendant_start_dates(tmp_path):
    """多被告人时，人工指定的起算日要能逐人给。"""
    from cedas.pipeline import plan, prepare

    lines = list(BASE_LINES)
    lines[4] = (
        "被告人李四，男，1990年1月2日出生，公民身份号码110101199001021234，"
        "汉族，初中文化，住北京市朝阳区某路2号。被告人王五，男，1991年2月3日出生，"
        "公民身份号码110101199102031234，汉族，初中文化，住北京市朝阳区某路3号。"
    )
    path = tmp_path / "两人.txt"
    path.write_text("\n".join(lines), encoding="utf-8")

    document = prepare(path)
    report = plan(
        document,
        mapping_path=MAPPING,
        require_template=False,
        start_dates={1: date(2023, 4, 1)},
    )

    first = next(item for item in report.items if item.seq == 1)
    assert first.start == date(2023, 4, 1)
    assert first.start_source == "人工指定"


def test_generate_with_prepared_matches_generate_with_path(template, tmp_path):
    """界面走 prepared 复用，命令行走路径。两条路的结果必须一致。"""
    from cedas.pipeline import prepare

    source = _judgment_text(tmp_path)
    document = prepare(source)

    kwargs = dict(mapping_path=MAPPING, template_path=template, when=WHEN, record=False)
    from_path = generate(source, output_dir=tmp_path / "a", **kwargs)
    from_prepared = generate(prepared=document, output_dir=tmp_path / "b", **kwargs)

    assert _text(from_path.items[0].path) == _text(from_prepared.items[0].path)
