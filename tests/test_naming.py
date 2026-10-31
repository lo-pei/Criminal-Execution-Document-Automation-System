"""文件命名规范：源文件改名与输出文书命名。

改名是本系统里**唯一会动用户磁盘上已有文件**的操作，所以断言不落在「函数没抛异常」，
而落在三件现场会出事的事上：

1. 改完文件还在不在、内容有没有变；
2. 同名时会不会覆盖别人的文件；
3. 案号没认出来时会不会硬改出一个以后还要再改一次的名字。

命名规则本身是纯函数，与 Tkinter 无关，本文件不需要图形环境。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cedas.domain.models import ProseParagraph
from cedas.extract.doc_type import detect_doc_kind
from cedas.pipeline import plan, prepare
from cedas.storage.naming import (
    NamePlan,
    RenameError,
    apply_rename,
    canonical_filename,
    canonical_stem,
    party_label,
    plan_rename,
    sanitize,
)
from cedas.ui import review

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "S000__京0105刑初123号.txt"
CASE_NO = "（2023）京0105刑初123号"
WHEN = date(2023, 5, 1)


# ---------------------------------------------------------------------------
# 名字怎么拼
# ---------------------------------------------------------------------------


def test_canonical_filename_single_defendant():
    name = canonical_filename(CASE_NO, "张三", "刑事判决书", WHEN, suffix=".pdf")
    assert name == "（2023）京0105刑初123号_张三_刑事判决书_20230501.pdf"


def test_party_label_collapses_co_defendants():
    """多人只写第一被告人 + 人数：名单写全会把案号挤出目录可见区。"""
    assert party_label(["张三"]) == "张三"
    assert party_label(["张三", "李四", "王五"]) == "张三等3人"
    assert party_label([]) == ""


def test_canonical_stem_skips_missing_segments():
    """缺项跳过而不写占位符：占位符会被检索当成关键词。"""
    assert canonical_stem(CASE_NO, "", "", None) == CASE_NO


def test_version_goes_to_the_end():
    stem = canonical_stem(CASE_NO, "张三", "执行通知书", WHEN, version=3)
    assert stem.endswith("_v3")
    assert "执行通知书" in stem.split("_")[2]


def test_fullwidth_parentheses_are_kept():
    """全角括号在 Windows 上合法，也是法院写案号的习惯，不替换。"""
    assert sanitize(CASE_NO) == CASE_NO


def test_long_case_no_is_truncated_not_dropped():
    long_no = "（2023）京0105刑初" + "1" * 60 + "号"
    name = canonical_filename(long_no, "张三", "刑事判决书", WHEN)
    assert len(name) < 120
    assert name.endswith("_20230501.docx")


# ---------------------------------------------------------------------------
# 改名方案
# ---------------------------------------------------------------------------


def test_plan_without_case_no_refuses(tmp_path):
    """案号是检索主键。认不出来就先不改，免得改两次。"""
    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4")

    name_plan = plan_rename(source, case_no="", party="张三", doc_type="刑事判决书", when=WHEN)
    assert not name_plan.ok
    assert "案号" in name_plan.missing
    assert "案号" in name_plan.reason
    with pytest.raises(RenameError):
        apply_rename(name_plan)


def test_plan_for_already_canonical_file(tmp_path):
    source = tmp_path / canonical_filename(CASE_NO, "张三", "刑事判决书", WHEN, suffix=".pdf")
    source.write_bytes(b"%PDF-1.4")

    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    assert name_plan.ok
    assert not name_plan.needs_rename


def test_plan_steps_around_name_clash(tmp_path):
    """同名不同内容：加版本号，绝不覆盖。"""
    taken = tmp_path / canonical_filename(CASE_NO, "张三", "刑事判决书", WHEN, suffix=".pdf")
    taken.write_bytes(b"another document")

    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4 first scan")

    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    assert name_plan.target is not None
    assert name_plan.target.name.endswith("_v2.pdf")
    assert not name_plan.duplicate


def test_plan_flags_duplicate_content(tmp_path):
    """同名同内容：同一份文书留两份，日后分不清底稿，所以只报告不改。"""
    canonical = canonical_filename(CASE_NO, "张三", "刑事判决书", WHEN, suffix=".pdf")
    body = b"%PDF-1.4 identical bytes"
    (tmp_path / canonical).write_bytes(body)
    source = tmp_path / "微信图片_20230510.pdf"
    source.write_bytes(body)

    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    assert name_plan.duplicate
    assert not name_plan.needs_rename
    with pytest.raises(RenameError):
        apply_rename(name_plan)


def test_same_size_different_content_is_a_clash_not_a_duplicate(tmp_path):
    """大小相同、内容不同是真实存在的（同一天扫的两份文书页数一样）。"""
    canonical = canonical_filename(CASE_NO, "张三", "刑事判决书", WHEN, suffix=".pdf")
    (tmp_path / canonical).write_bytes(b"AAAA")
    source = tmp_path / "扫描件.pdf"
    source.write_bytes(b"BBBB")

    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    assert not name_plan.duplicate
    assert name_plan.target is not None and name_plan.target.name.endswith("_v2.pdf")


# ---------------------------------------------------------------------------
# 真改
# ---------------------------------------------------------------------------


def test_apply_rename_moves_and_keeps_content(tmp_path):
    source = tmp_path / "扫描件001.pdf"
    body = b"%PDF-1.4 judgment"
    source.write_bytes(body)

    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    target = apply_rename(name_plan)

    assert not source.exists()
    assert target.exists()
    assert target.read_bytes() == body
    assert target.parent == tmp_path, "改名只在原目录内进行，不跨目录搬文件"


def test_apply_rename_is_idempotent(tmp_path):
    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4")

    first = apply_rename(
        plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)
    )
    again = plan_rename(
        first, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN
    )
    assert not again.needs_rename
    assert apply_rename(again) == first


def test_missing_source_reports_in_plain_words(tmp_path):
    plan_ = NamePlan(source=tmp_path / "没了.pdf", target=tmp_path / f"{CASE_NO}.pdf")
    with pytest.raises(RenameError) as error:
        apply_rename(plan_)
    assert "找不到" in str(error.value)


# ---------------------------------------------------------------------------
# 文书类型识别
# ---------------------------------------------------------------------------


def _paragraphs(*lines: str) -> list[ProseParagraph]:
    return [ProseParagraph(index=i, text=text) for i, text in enumerate(lines)]


def test_detect_title_line():
    assert detect_doc_kind(_paragraphs("北京市朝阳区人民法院", "刑事判决书")).name == "刑事判决书"


def test_detect_prefers_attached_civil():
    """「附带民事」必须比「判决书」先命中，否则名字会把赔偿部分丢掉。"""
    kind = detect_doc_kind(_paragraphs("刑事附带民事判决书"))
    assert kind.name == "刑事附带民事判决书"


def test_detect_from_main_text_when_title_missing():
    kind = detect_doc_kind(_paragraphs("本院认为，被告人张三犯盗窃罪。", "判决如下："))
    assert kind.name == "刑事判决书"
    assert kind.confidence < 1.0


def test_detect_returns_empty_when_unknown():
    kind = detect_doc_kind(_paragraphs("送达回证", "签收人：李四"))
    assert kind.name == ""
    assert not kind.known


def test_detect_ignores_appeal_sentence():
    """「如不服本判决」含「判决」但不是标题，不能被当成标题行。"""
    kind = detect_doc_kind(_paragraphs("如不服本判决，可在接到判决书第二日起十日内上诉"))
    assert kind.name == ""


# ---------------------------------------------------------------------------
# 接进流程
# ---------------------------------------------------------------------------


def test_prepared_document_suggests_canonical_name(tmp_path):
    source = tmp_path / "扫描件001.txt"
    source.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    document = prepare(source)
    name_plan = document.name_plan()

    assert document.doc_kind.name == "刑事判决书"
    assert name_plan.suggested == "（2023）京0105刑初123号_张三_刑事判决书_20230501.txt"
    assert name_plan.missing == ()


def test_rename_source_updates_prepared_document(tmp_path):
    source = tmp_path / "扫描件001.txt"
    source.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    document = prepare(source)
    path = document.rename_source()

    assert path.exists()
    assert document.source == path
    assert document.name_plan().needs_rename is False, "改完再看，名字应当已经规范"


def test_report_carries_suggested_name(tmp_path):
    source = tmp_path / "扫描件001.txt"
    source.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    report = plan(prepare(source), require_template=False)
    assert report.suggested_name == "（2023）京0105刑初123号_张三_刑事判决书_20230501.txt"
    assert report.to_dict()["suggested_name"] == report.suggested_name


# ---------------------------------------------------------------------------
# 界面用语
# ---------------------------------------------------------------------------


def test_rename_prompt_shows_before_and_after(tmp_path):
    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4")
    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN)

    prompt = review.rename_prompt(name_plan)
    assert prompt.can_apply
    assert "扫描件001.pdf" in prompt.body
    assert name_plan.suggested in prompt.body


def test_rename_prompt_mentions_missing_fields(tmp_path):
    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4")
    name_plan = plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=None)

    prompt = review.rename_prompt(name_plan)
    assert prompt.can_apply
    assert "判决日期" in prompt.body


def test_rename_prompt_never_leaks_codes(tmp_path):
    """界面不出现错误码与技术词。"""
    source = tmp_path / "扫描件001.pdf"
    source.write_bytes(b"%PDF-1.4")
    for name_plan in (
        plan_rename(source, case_no="", party="张三", doc_type="", when=None),
        plan_rename(source, case_no=CASE_NO, party="张三", doc_type="刑事判决书", when=WHEN),
    ):
        prompt = review.rename_prompt(name_plan)
        text = f"{prompt.title}{prompt.body}"
        for word in ("E-", "阻断", "None", "NamePlan", "plan_rename"):
            assert word not in text
